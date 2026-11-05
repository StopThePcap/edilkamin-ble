"""Modbus protocol encoding/decoding for Edilkamin BLE."""

import struct
import time
from dataclasses import dataclass
from typing import Optional

from .const import FIXED_KEY, PADDING_OUT
from .crypto import EdilkaminCrypto


class ProtocolError(Exception):
    """Raised when a response does not match the request that produced it."""

    pass


@dataclass
class Datagram:
    """Represents the 32-byte datagram structure.

    Structure:
        - timestamp: 4 bytes (Unix timestamp)
        - fixed_key: 16 bytes
        - payload: 6 bytes (Modbus command)
        - checksum: 2 bytes (CRC16-Modbus)
        - padding: 4 bytes (0x04 for outbound)
    """

    timestamp: bytes
    fixed_key: bytes
    payload: bytes
    checksum: bytes
    padding: bytes

    def to_bytes(self) -> bytes:
        """Convert datagram to 32-byte array."""
        return self.timestamp + self.fixed_key + self.payload + self.checksum + self.padding

    @classmethod
    def from_bytes(cls, data: bytes) -> "Datagram":
        """Parse 32-byte array into datagram."""
        if len(data) != 32:
            raise ValueError(f"Datagram must be 32 bytes, got {len(data)}")

        return cls(
            timestamp=data[0:4],
            fixed_key=data[4:20],
            payload=data[20:26],
            checksum=data[26:28],
            padding=data[28:32],
        )


@dataclass
class Response:
    """Parsed response from stove.

    Response structure (after decryption):
        - timestamp: 4 bytes
        - fixed_key: 16 bytes
        - payload: 7 bytes (Modbus response)
        - checksum: 2 bytes
        - padding: 3 bytes (0x03)
    """

    timestamp: bytes
    fixed_key: bytes
    payload: bytes
    checksum: bytes
    padding: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> "Response":
        """Parse 32-byte response array."""
        if len(data) != 32:
            raise ValueError(f"Response must be 32 bytes, got {len(data)}")

        return cls(
            timestamp=data[0:4],
            fixed_key=data[4:20],
            payload=data[20:27],
            checksum=data[27:29],
            padding=data[29:32],
        )


class EdilkaminProtocol:
    """Handles Edilkamin Modbus-over-BLE protocol encoding/decoding."""

    def __init__(self, crypto: Optional[EdilkaminCrypto] = None):
        """Initialize protocol handler.

        Args:
            crypto: Crypto instance for encryption. Creates default if None.
        """
        self._crypto = crypto or EdilkaminCrypto()

    @staticmethod
    def crc16_modbus(data: bytes) -> bytes:
        """Calculate CRC16-Modbus checksum.

        Args:
            data: Data to calculate checksum for.

        Returns:
            2-byte checksum in little-endian format.
        """
        crc = 0xFFFF

        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1

        # Return as little-endian bytes
        return struct.pack("<H", crc)

    @staticmethod
    def get_timestamp() -> bytes:
        """Get current Unix timestamp as 4 bytes (big-endian)."""
        return struct.pack(">I", int(time.time()))

    def build_read_command(self, register: bytes) -> bytes:
        """Build encrypted read command packet.

        Args:
            register: 6-byte register definition (slave, func, reg_hi, reg_lo, len_hi, len_lo)

        Returns:
            32-byte encrypted packet ready to send via BLE.
        """
        if len(register) != 6:
            raise ValueError(f"Register must be 6 bytes, got {len(register)}")

        # Build datagram
        timestamp = self.get_timestamp()
        checksum = self.crc16_modbus(register)

        datagram = Datagram(
            timestamp=timestamp,
            fixed_key=FIXED_KEY,
            payload=register,
            checksum=checksum,
            padding=PADDING_OUT,
        )

        # Encrypt and return
        plaintext = datagram.to_bytes()
        return self._crypto.encrypt(plaintext)

    def build_write_command(self, register: bytes, value_hi: int, value_lo: int) -> bytes:
        """Build encrypted write command packet.

        Args:
            register: 4-byte register base (slave, func, reg_hi, reg_lo)
            value_hi: High byte of value to write
            value_lo: Low byte of value to write

        Returns:
            32-byte encrypted packet ready to send via BLE.
        """
        if len(register) != 4:
            raise ValueError(f"Register base must be 4 bytes, got {len(register)}")

        # Build 6-byte payload: register + value
        payload = register + bytes([value_hi, value_lo])

        # Build datagram
        timestamp = self.get_timestamp()
        checksum = self.crc16_modbus(payload)

        datagram = Datagram(
            timestamp=timestamp,
            fixed_key=FIXED_KEY,
            payload=payload,
            checksum=checksum,
            padding=PADDING_OUT,
        )

        # Encrypt and return
        plaintext = datagram.to_bytes()
        return self._crypto.encrypt(plaintext)

    def parse_response(self, encrypted_data: bytes) -> Response:
        """Parse encrypted response from stove.

        Args:
            encrypted_data: 32-byte encrypted response.

        Returns:
            Parsed Response object.
        """
        if len(encrypted_data) != 32:
            raise ValueError(f"Response must be 32 bytes, got {len(encrypted_data)}")

        decrypted = self._crypto.decrypt(encrypted_data)
        return Response.from_bytes(decrypted)

    @staticmethod
    def validate_read_response(response: Response, register: bytes) -> None:
        """Check that a response actually answers the read it was matched with.

        Notifications are matched to requests by arrival order, so a late reply
        from a timed-out exchange would otherwise be accepted as the answer to
        the current one and shift every following register read by one.

        Args:
            response: Parsed response object.
            register: The 6-byte register definition that was sent.

        Raises:
            ProtocolError: If the response does not echo the request.
        """
        payload = response.payload
        if len(payload) < 5:
            raise ProtocolError(f"Response payload too short: {payload.hex()}")

        expected_slave = register[0]
        expected_function = register[1]

        if payload[0] != expected_slave or payload[1] != expected_function:
            raise ProtocolError(
                f"Response does not match request: expected slave/function "
                f"{expected_slave:02x}/{expected_function:02x}, got "
                f"{payload[0]:02x}/{payload[1]:02x} (payload {payload.hex()})"
            )

        # A read of N registers answers with 2*N data bytes. Long reads are
        # truncated to what fits in the 32-byte datagram, so an exact match is
        # only required when the answer could fit in the first place.
        expected_byte_count = 2 * ((register[4] << 8) | register[5])
        room = len(payload) - 3
        if payload[2] < 2 or (
            expected_byte_count <= room and payload[2] != expected_byte_count
        ):
            raise ProtocolError(
                f"Unexpected byte count {payload[2]} (expected "
                f"{expected_byte_count}) in response {payload.hex()}"
            )

    def extract_register_value(self, response: Response) -> int:
        """Extract register value from response.

        For read responses, the value is typically in payload bytes 3-4.
        Response payload format: [slave, func, byte_count, data_hi, data_lo, ...]

        Args:
            response: Parsed response object.

        Returns:
            16-bit register value.
        """
        # Modbus read response format:
        # [0] = slave id
        # [1] = function code (0x03)
        # [2] = byte count
        # [3] = data high byte
        # [4] = data low byte
        if len(response.payload) >= 5:
            return (response.payload[3] << 8) | response.payload[4]
        return 0

    def extract_status_bytes(self, response: Response) -> tuple[int, int]:
        """Extract status bytes from response.

        Used for parsing stove status where both bytes are significant.

        Args:
            response: Parsed response object.

        Returns:
            Tuple of (status_byte_1, status_byte_2)
        """
        if len(response.payload) >= 5:
            return (response.payload[3], response.payload[4])
        return (0, 0)
