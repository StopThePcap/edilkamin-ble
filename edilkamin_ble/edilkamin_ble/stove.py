"""Stove state management and high-level control interface."""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .ble_client import BLECommandError, BLEConnectionError, EdilkaminBLEClient
from .const import (
    FAN_MODES,
    PELLET_LEVEL,
    POWER_LEVELS,
    REG_READ_ALARM,
    REG_READ_AUTOMATIC,
    REG_READ_FIREPLACE_MAIN_STATUS,
    REG_READ_MAIN_ENV_TEMP,
    REG_READ_ON_OFF_CHRONO,
    REG_READ_PELLET_SENSOR,
    REG_READ_POWER,
    REG_READ_RELAX,
    REG_READ_STANDBY_STATUS,
    REG_READ_TEMPERATURE,
    REG_READ_THERMOCOUPLE_TEMP,
    REG_SET_CHANGE_AUTO_SWITCH,
    REG_SET_FAN_1,
    REG_SET_ON_OFF,
    REG_SET_ON_OFF_CHRONO,
    REG_SET_RELAX_STATUS,
    REG_SET_STANDBY_STATUS,
    REG_SET_WRITE_NEW_POWER,
    REG_SET_WRITE_NEW_TEMP,
    IGNITION_SUB_PHASES,
    OPERATIONAL_PHASES,
)
from .protocol import EdilkaminProtocol, ProtocolError

_LOGGER = logging.getLogger(__name__)


class StovePhase(Enum):
    """Stove operating phases (ignition broken down by sub-phase)."""

    OFF = "off"
    # Ignition, by sub-phase; IGNITION itself is an undocumented sub-phase.
    IGNITION = "ignition"
    CLEANING = "cleaning"
    LOADING_PELLETS = "loading_pellets"
    LOADING_PAUSE = "loading_pause"
    CHECKING_EXHAUST = "checking_exhaust"
    THRESHOLD_CHECK = "threshold_check"
    WARMUP = "warmup"
    RUNNING = "running"
    SHUTDOWN = "shutdown"
    COOLING = "cooling"
    FINAL_CLEANING = "final_cleaning"
    UNKNOWN = "unknown"

    @property
    def is_ignition(self) -> bool:
        return self in _IGNITION_PHASES


_IGNITION_PHASES = frozenset({
    StovePhase.IGNITION,
    StovePhase.CLEANING,
    StovePhase.LOADING_PELLETS,
    StovePhase.LOADING_PAUSE,
    StovePhase.CHECKING_EXHAUST,
    StovePhase.THRESHOLD_CHECK,
    StovePhase.WARMUP,
})


@dataclass
class StoveState:
    """Current state of the stove."""

    # Power state
    power_on: bool = False
    phase: StovePhase = StovePhase.OFF

    # Temperatures (in Celsius)
    current_temperature: float = 0.0
    target_temperature: float = 20.0
    exhaust_temperature: float = 0.0

    # Power and fan
    power_level: int = 1  # 1-5
    fan_speed: int = 1  # 1-6 (6=auto)
    automatic_mode: bool = False

    # Features
    relax_mode: bool = False
    chrono_mode: bool = False
    standby_mode: bool = False

    # Status
    alarm: bool = False
    alarm_code: int = 0
    pellet_level: str = "ok"  # "ok" or "empty"

    # Raw status bytes for debugging
    status_bytes: tuple[int, int] = field(default_factory=lambda: (0, 0))


class EdilkaminStove:
    """High-level interface for controlling an Edilkamin stove."""

    def __init__(self, ble_client: EdilkaminBLEClient, protocol: Optional[EdilkaminProtocol] = None):
        """Initialize stove controller.

        Args:
            ble_client: BLE client for communication.
            protocol: Protocol handler. Creates default if None.
        """
        self._ble = ble_client
        self._protocol = protocol or EdilkaminProtocol()
        self._state = StoveState()
        # Serialises whole operations. The BLE client already serialises single
        # exchanges; this additionally stops a command from landing in the
        # middle of a status poll.
        self._op_lock = asyncio.Lock()

    @property
    def state(self) -> StoveState:
        """Return current stove state."""
        return self._state

    @property
    def connected(self) -> bool:
        """Return True if connected to stove."""
        return self._ble.connected

    async def connect(self) -> bool:
        """Connect to the stove."""
        return await self._ble.connect()

    async def disconnect(self) -> None:
        """Disconnect from the stove."""
        await self._ble.disconnect()

    async def _send_read(self, register: bytes) -> tuple[int, int]:
        """Send a read command and return status bytes.

        Args:
            register: 6-byte register definition.

        Returns:
            Tuple of (byte1, byte2) from response.

        Raises:
            ProtocolError: If the response does not answer this request.
        """
        command = self._protocol.build_read_command(register)
        response_data = await self._ble.send_command(command)
        response = self._protocol.parse_response(response_data)
        self._protocol.validate_read_response(response, register)
        return self._protocol.extract_status_bytes(response)

    async def _send_write(self, register: bytes, value_hi: int, value_lo: int) -> bool:
        """Send a write command.

        Args:
            register: 4-byte register base.
            value_hi: High byte of value.
            value_lo: Low byte of value.

        Returns:
            True if successful.
        """
        command = self._protocol.build_write_command(register, value_hi, value_lo)
        await self._ble.send_command(command)
        return True

    async def poll_status(self) -> StoveState:
        """Poll all status registers and update state.

        Returns:
            Updated StoveState.
        """
        async with self._op_lock:
            return await self._poll_status()

    async def _poll_status(self) -> StoveState:
        """Poll all status registers. Caller must hold the operation lock."""
        _LOGGER.debug("Polling stove status")

        try:
            # Read main status
            status = await self._send_read(REG_READ_FIREPLACE_MAIN_STATUS)
            self._state.status_bytes = status
            self._state.phase = self._parse_phase(status)
            if self._state.phase is StovePhase.UNKNOWN:
                # An unrecognised phase says nothing about whether the stove is
                # burning, so keep the last known answer instead of guessing
                # "on" (which is what "not in the off set" would give).
                _LOGGER.warning(
                    "Unrecognised stove status bytes %s — keeping previous power state",
                    status,
                )
            else:
                self._state.power_on = (
                    self._state.phase is StovePhase.RUNNING
                    or self._state.phase.is_ignition
                )

            # Read temperatures
            temp_bytes = await self._send_read(REG_READ_MAIN_ENV_TEMP)
            self._state.current_temperature = self._bytes_to_temp(temp_bytes)

            target_bytes = await self._send_read(REG_READ_TEMPERATURE)
            self._state.target_temperature = self._bytes_to_temp(target_bytes)

            exhaust_bytes = await self._send_read(REG_READ_THERMOCOUPLE_TEMP)
            self._state.exhaust_temperature = self._bytes_to_temp(exhaust_bytes)

            # Power level and fan speed live in the same register (0x0529):
            # (fan_speed, power_level). One round trip, not two.
            power_fan_bytes = await self._send_read(REG_READ_POWER)
            self._state.fan_speed = (
                power_fan_bytes[0] if power_fan_bytes[0] in range(1, 7) else 1
            )
            self._state.power_level = (
                power_fan_bytes[1] if power_fan_bytes[1] in range(1, 6) else 1
            )

            # Read automatic mode
            auto_bytes = await self._send_read(REG_READ_AUTOMATIC)
            self._state.automatic_mode = auto_bytes[1] == 1

            # Read relax mode
            relax_bytes = await self._send_read(REG_READ_RELAX)
            self._state.relax_mode = relax_bytes[0] == 1 and relax_bytes[1] == 1

            # Read chrono mode
            chrono_bytes = await self._send_read(REG_READ_ON_OFF_CHRONO)
            self._state.chrono_mode = chrono_bytes[1] == 1

            # Read standby mode
            standby_bytes = await self._send_read(REG_READ_STANDBY_STATUS)
            self._state.standby_mode = standby_bytes[1] == 1

            # Read alarm status
            alarm_bytes = await self._send_read(REG_READ_ALARM)
            # The code is in the HIGH byte: after a power outage the stove
            # reported (21, 0), alarm 21 "power outage", and refused to start
            # while HA showed "No alarm" from the low byte. Fall back to the
            # low byte in case other firmware puts it there.
            code = alarm_bytes[0] or alarm_bytes[1]
            if code and code != self._state.alarm_code:
                _LOGGER.warning("Stove alarm %d (raw bytes %s)", code, alarm_bytes)
            self._state.alarm = code != 0
            self._state.alarm_code = code

            # Read pellet level
            pellet_bytes = await self._send_read(REG_READ_PELLET_SENSOR)
            if pellet_bytes[1] not in PELLET_LEVEL:
                _LOGGER.debug("Unknown pellet sensor value: %s", pellet_bytes[1])
            self._state.pellet_level = PELLET_LEVEL.get(pellet_bytes[1], "ok")

            _LOGGER.debug("Status poll complete: %s", self._state)

        except (BLEConnectionError, BLECommandError, ProtocolError) as e:
            _LOGGER.error("Failed to poll status: %s", e)
            raise

        return self._state

    def _parse_phase(self, status_bytes: tuple[int, int]) -> StovePhase:
        """Parse the status register into a StovePhase.

        The register holds (sub_phase, phase). The sub-phase only has a
        documented meaning during ignition. Matching exact pairs, as before,
        turned every other running sub-value, e.g. (4, 2), into "unknown".
        """
        sub_phase, phase = status_bytes
        name = OPERATIONAL_PHASES.get(phase)
        if name == "ignition":
            name = IGNITION_SUB_PHASES.get(sub_phase, "ignition")
        if name is None:
            return StovePhase.UNKNOWN
        return StovePhase(name)

    def _bytes_to_temp(self, temp_bytes: tuple[int, int]) -> float:
        """Convert temperature bytes to Celsius value."""
        # Temperature is typically (high_byte << 8 | low_byte) / 10
        raw_value = (temp_bytes[0] << 8) | temp_bytes[1]
        return raw_value / 10.0

    def _temp_to_bytes(self, temp: float) -> tuple[int, int]:
        """Convert Celsius temperature to bytes."""
        raw_value = int(temp * 10)
        return ((raw_value >> 8) & 0xFF, raw_value & 0xFF)

    # =========================================================================
    # Control methods
    # =========================================================================

    async def set_power(self, on: bool) -> bool:
        """Turn stove on or off.

        Args:
            on: True to turn on, False to turn off.

        Returns:
            True if successful.
        """
        _LOGGER.info("Setting power: %s", "ON" if on else "OFF")
        async with self._op_lock:
            # REG_SET_ON_OFF: B5=0, B6=1=on, 0=off
            await self._send_write(REG_SET_ON_OFF, 0, 1 if on else 0)
        return True

    async def set_temperature(self, temp: float) -> bool:
        """Set target temperature.

        Args:
            temp: Target temperature in Celsius (typically 10-30).

        Returns:
            True if successful.
        """
        temp = max(10.0, min(30.0, temp))  # Clamp to valid range
        _LOGGER.info("Setting temperature: %.1f°C", temp)

        hi, lo = self._temp_to_bytes(temp)
        async with self._op_lock:
            await self._send_write(REG_SET_WRITE_NEW_TEMP, hi, lo)
        return True

    async def set_power_level(self, level: int) -> bool:
        """Set power level (P1-P5).

        Args:
            level: Power level 1-5.

        Returns:
            True if successful.
        """
        level = max(1, min(5, level))
        _LOGGER.info("Setting power level: P%d", level)

        async with self._op_lock:
            # REG_SET_WRITE_NEW_POWER: B5=fan1Value, B6=PowerLevel
            await self._send_write(REG_SET_WRITE_NEW_POWER, self._state.fan_speed, level)
        return True

    async def set_fan_speed(self, speed: int) -> bool:
        """Set fan speed.

        Args:
            speed: Fan speed 1-5 (20%-100%) or 6 for auto.

        Returns:
            True if successful.
        """
        speed = max(1, min(6, speed))
        _LOGGER.info("Setting fan speed: %s", FAN_MODES.get(speed, speed))

        async with self._op_lock:
            # REG_SET_FAN_1: B5=Power 0-6, B6=PowerLevel
            await self._send_write(REG_SET_FAN_1, speed, self._state.power_level)
        return True

    async def set_automatic_mode(self, enabled: bool) -> bool:
        """Enable or disable automatic mode.

        Args:
            enabled: True to enable automatic mode.

        Returns:
            True if successful.
        """
        _LOGGER.info("Setting automatic mode: %s", enabled)
        async with self._op_lock:
            # UNVERIFIED: this sends the 16-bit value 0x0101, whereas
            # set_power sends a plain 1 to its register. One of the two
            # encodings is likely wrong, but confirming it needs a capture from
            # a real stove, so the existing behaviour is left alone.
            # REG_SET_CHANGE_AUTO_SWITCH: B5=0/1=off/on
            await self._send_write(
                REG_SET_CHANGE_AUTO_SWITCH, 1 if enabled else 0, 1 if enabled else 0
            )
        return True

    async def set_relax_mode(self, enabled: bool) -> bool:
        """Enable or disable relax mode (silent fan).

        Args:
            enabled: True to enable relax mode.

        Returns:
            True if successful.
        """
        _LOGGER.info("Setting relax mode: %s", enabled)
        val = 1 if enabled else 0
        async with self._op_lock:
            # REG_SET_RELAX_STATUS: B5=0/1, B6=0/1 (off=00, on=11)
            await self._send_write(REG_SET_RELAX_STATUS, val, val)
        return True

    async def set_chrono_mode(self, enabled: bool) -> bool:
        """Enable or disable chrono/timer mode.

        Args:
            enabled: True to enable chrono mode.

        Returns:
            True if successful.
        """
        _LOGGER.info("Setting chrono mode: %s", enabled)
        async with self._op_lock:
            # REG_SET_ON_OFF_CHRONO: B5=ChronoProgram, B6=0/1=on/off
            await self._send_write(REG_SET_ON_OFF_CHRONO, 0, 1 if enabled else 0)
        return True

    async def set_standby_mode(self, enabled: bool) -> bool:
        """Enable or disable standby mode.

        Args:
            enabled: True to enable standby mode.

        Returns:
            True if successful.
        """
        _LOGGER.info("Setting standby mode: %s", enabled)
        async with self._op_lock:
            # UNVERIFIED: sends 0x0100 for "on". See set_automatic_mode.
            # REG_SET_STANDBY_STATUS: B5=0/1
            await self._send_write(REG_SET_STANDBY_STATUS, 1 if enabled else 0, 0)
        return True

    def get_power_level_name(self) -> str:
        """Get human-readable power level name."""
        return POWER_LEVELS.get(self._state.power_level, f"P{self._state.power_level}")

    def get_fan_mode_name(self) -> str:
        """Get human-readable fan mode name."""
        return FAN_MODES.get(self._state.fan_speed, str(self._state.fan_speed))

    def get_phase_name(self) -> str:
        """Get human-readable phase name."""
        return self._state.phase.value.replace("_", " ").title()
