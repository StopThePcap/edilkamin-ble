"""BLE client for Edilkamin stove communication."""

import asyncio
import contextlib
import logging
import sys
from typing import Optional, Union

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from . import mgmt
from .bluez import REASON_AUTHENTICATION, BlueZ, BlueZDeviceState, BlueZUnavailableError
from .const import (
    BLE_CONNECT_TIMEOUT,
    BLE_NOTIFY_CHAR_UUID,
    BLE_SCAN_TIMEOUT,
    BLE_WRITE_CHAR_UUID,
    DEFAULT_COMMAND_TIMEOUT,
    DEVICE_NAME_PREFIX,
    PAIRING_CONNECT_TIMEOUT,
    PAIRING_RETRY_DELAY,
    PAIRING_SCAN_TIMEOUT,
    PAIRING_WINDOW,
)

_LOGGER = logging.getLogger(__name__)


class BLEConnectionError(Exception):
    """Raised when BLE connection fails."""

    pass


class BLEAdapterUnavailableError(BLEConnectionError):
    """Raised when BlueZ has no powered Bluetooth adapter.

    Nothing about the stove or the pairing is wrong in this case, so callers
    must not treat it as a pairing problem.
    """

    pass


class BLENotPairedError(BLEConnectionError):
    """Raised when BlueZ holds no bond for the stove and pairing failed.

    The stove only accepts pairing for a short window after it powers up, so
    this needs the user: start pairing mode and power-cycle the stove.
    """

    pass


class BLEHandshakeDropError(BLEConnectionError):
    """Raised when the stove dropped a bonded connection during setup.

    ``reason`` is the BlueZ disconnect reason when bluetoothd reported one.
    An authentication failure means the stove no longer has our keys (it can
    lose them after a reset). Any other reason (a timeout, an adapter reset,
    the phone app holding the stove) is not evidence of a stale bond, so the
    bond is never removed automatically on the strength of it.
    ``authentication_failure`` (the stove reported it has no key) is the one
    case where the main loop re-pairs by itself.
    """

    def __init__(self, message: str, reason: Optional[str] = None):
        super().__init__(message)
        self.reason = reason

    @property
    def authentication_failure(self) -> bool:
        return self.reason == REASON_AUTHENTICATION


class BLECommandError(Exception):
    """Raised when BLE command fails or times out."""

    pass


class EdilkaminBLEClient:
    """BLE client for communicating with Edilkamin stoves."""

    def __init__(
        self,
        mac_address: str,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    ):
        """Initialize BLE client.

        Reconnect scheduling lives in the add-on's main loop, so this class
        only ever makes a single connection attempt per connect() call.

        Args:
            mac_address: Bluetooth MAC address of the stove.
            command_timeout: Timeout for command responses in seconds.
        """
        self._mac_address = mac_address.upper()
        self._command_timeout = command_timeout

        self._client: Optional[BleakClient] = None
        self._response_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._connected = False
        self._connection_lock = asyncio.Lock()
        # Serialises write-and-await-response pairs. Without this, a command
        # coming in over MQTT can interleave with the polling loop and each
        # side picks up the other's notification.
        self._command_lock = asyncio.Lock()
        # True only between the BleakClient.connect() call and the end of
        # connection setup, so _on_disconnect can tell "the stove dropped us
        # mid-handshake" from every other kind of disconnect.
        self._connect_in_progress = False
        self._disconnected_during_connect = False

        self._bluez: Optional[BlueZ] = None
        if sys.platform == "linux":
            self._bluez = BlueZ(self._mac_address)
            self._bluez.set_disconnect_callback(self._on_bluez_disconnect)
        self._last_disconnect_reason: Optional[str] = None
        # Set when BlueZ reports an authentication failure during a connect
        # attempt. Sticky for the attempt: BlueZ retries a failed LE connect
        # by itself, so by the time the attempt ends the *last* reason is
        # usually our own timeout ("Local"), not the rejection that matters.
        self._auth_rejected = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Return True if connected to stove."""
        return self._connected and self._client is not None and self._client.is_connected

    @property
    def mac_address(self) -> str:
        """Return the MAC address."""
        return self._mac_address

    def _notification_handler(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        """Handle incoming BLE notifications.

        Args:
            sender: Characteristic that sent the notification.
            data: Notification data.
        """
        _LOGGER.debug("Received notification: %s bytes", len(data))
        self._response_queue.put_nowait(bytes(data))

    def _drain_response_queue(self) -> None:
        """Discard any buffered notifications.

        Anything still queued belongs to an earlier (timed out or aborted)
        exchange. Handing it to the next command would silently shift every
        subsequent read by one register.
        """
        dropped = 0
        while True:
            try:
                self._response_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            dropped += 1
        if dropped:
            _LOGGER.debug("Discarded %d stale notification(s)", dropped)

    async def _bluez_state(self) -> Optional[BlueZDeviceState]:
        """Return BlueZ's view of the adapter and stove, or None off Linux."""
        if self._bluez is None:
            return None
        try:
            return await self._bluez.get_state()
        except BlueZUnavailableError as e:
            raise BLEAdapterUnavailableError(str(e)) from e

    async def _apply_conn_params(self, state: Optional[BlueZDeviceState]) -> None:
        """Give the next link a supervision timeout that survives a weak signal.

        Done before every connect: removing the device (re-pairing) also drops
        stored connection parameters. See mgmt.py.
        """
        if state is None or state.adapter_path is None:
            return
        await asyncio.to_thread(
            mgmt.load_conn_params, state.adapter_path, self._mac_address
        )

    def _on_bluez_disconnect(self, reason: str, message: str) -> None:
        self._last_disconnect_reason = reason
        if reason == REASON_AUTHENTICATION and self._connect_in_progress:
            self._auth_rejected.set()

    async def connect(self) -> bool:
        """Connect to the stove.

        If BlueZ holds no bond for the stove, pairing is attempted as part of
        the connection, with a pairing agent registered (see bluez_agent.py).
        If that fails, BLENotPairedError is raised; pairing mode (pair())
        retries for longer while the user power-cycles the stove.

        Returns:
            True if connection successful.

        Raises:
            BLEAdapterUnavailableError: No powered Bluetooth adapter.
            BLENotPairedError: No bond, and pairing failed.
            BLEHandshakeDropError: Bonded, but the stove dropped the link.
            BLEConnectionError: Any other connection failure.
        """
        async with self._connection_lock:
            if self.connected:
                return True

            # Tear the previous client down *before* arming handshake-drop
            # detection: the BlueZ backend fires disconnected_callback for our
            # own disconnect() calls too.
            await self._teardown_client()

            _LOGGER.info("Connecting to Edilkamin stove at %s", self._mac_address)

            state = await self._bluez_state()
            if state is not None:
                if not state.adapter_present:
                    raise BLEAdapterUnavailableError(
                        "No powered Bluetooth adapter found. Check that the "
                        "adapter is attached to this host (bluetoothctl list)."
                    )
                if state.connected:
                    _LOGGER.info("Dropping a stale BlueZ connection to the stove")
                    await self._bluez.disconnect_device(state)
                    await asyncio.sleep(1)

            await self._apply_conn_params(state)

            needs_pairing = state is not None and not state.bonded
            if needs_pairing:
                _LOGGER.info(
                    "No bond for %s yet, attempting to pair",
                    self._mac_address,
                )

            device = await self._find_device(BLE_SCAN_TIMEOUT)
            try:
                async with self._pairing_agent(needs_pairing):
                    await self._open(
                        device or self._mac_address,
                        pair=needs_pairing,
                        timeout=BLE_CONNECT_TIMEOUT,
                    )
            except Exception as e:
                await self._close_failed_client()
                raise await self._classify_failure(e, needs_pairing) from e

            _LOGGER.info("Connected to Edilkamin stove successfully")
            return True

    async def _open(
        self,
        target: Union[BLEDevice, str],
        pair: bool,
        timeout: float,
    ) -> None:
        """Run one BleakClient connect, service check and notify subscribe.

        Pairing happens over bleak's own D-Bus connection and on the same link
        that is then used for GATT. The old approach (a one-shot
        ``bluetoothctl pair`` beforehand, then a second connection from bleak)
        could see that second link dropped right after a successful pairing.
        """
        self._drain_response_queue()
        self._disconnected_during_connect = False
        self._last_disconnect_reason = None
        self._auth_rejected.clear()

        self._client = BleakClient(
            target,
            disconnected_callback=self._on_disconnect,
            timeout=timeout,
            pair=pair,
        )

        self._connect_in_progress = True
        try:
            _LOGGER.info("Initiating BLE connection%s...", " with pairing" if pair else "")
            await self._connect_unless_rejected(self._client)
            _LOGGER.info("Initial connection established, checking services...")

            service_found = False
            for service in self._client.services:
                _LOGGER.debug("Found service: %s", service.uuid)
                if "abf0" in service.uuid.lower():
                    service_found = True
                    _LOGGER.info("Found Edilkamin service: %s", service.uuid)

            if not service_found:
                _LOGGER.warning("Edilkamin service (abf0) not found in services")

            _LOGGER.info("Subscribing to notifications...")
            await self._client.start_notify(
                BLE_NOTIFY_CHAR_UUID,
                self._notification_handler,
            )
        finally:
            self._connect_in_progress = False

        self._connected = True

    def _pairing_agent(self, active: bool = True):
        """Context in which BlueZ can confirm Just Works pairing with the stove.

        Without a registered agent, bluetoothd refuses the confirmation the
        kernel asks for and the stove's pairing fails with SMP "Numeric
        Comparison Failed". See bluez_agent.py.
        """
        if active and self._bluez is not None:
            return self._bluez.pairing_agent()
        return contextlib.nullcontext()

    async def _connect_unless_rejected(self, client: BleakClient) -> None:
        """Connect, but give up as soon as the stove rejects our keys.

        With a key the stove has discarded, BlueZ keeps re-establishing the
        link and failing encryption ("PIN or Key Missing") until bleak's
        connect timeout, about 40 futile reconnects in 30 seconds. One
        rejection is conclusive, so stop there.
        """
        connect = asyncio.ensure_future(client.connect())
        rejected = asyncio.ensure_future(self._auth_rejected.wait())
        try:
            await asyncio.wait({connect, rejected}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            rejected.cancel()
        if connect.done():
            connect.result()
            return
        connect.cancel()
        await asyncio.gather(connect, return_exceptions=True)
        self._disconnected_during_connect = True
        raise BleakError("The stove rejected our pairing keys")

    async def _close_failed_client(self) -> None:
        """Disconnect and forget a client whose connection setup failed."""
        self._connected = False
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:
            pass

    async def _classify_failure(
        self, error: Exception, attempted_pairing: bool
    ) -> BLEConnectionError:
        """Turn a raw connect failure into the error the main loop acts on."""
        if isinstance(error, BLEConnectionError):
            return error

        # A dongle that crashes off the USB bus produces exactly the same
        # "connected, then dropped" pattern as a stale bond. Check the adapter
        # before blaming the pairing.
        try:
            state = await self._bluez_state()
        except BLEAdapterUnavailableError as e:
            return e
        if state is not None and not state.adapter_present:
            return BLEAdapterUnavailableError(
                f"Bluetooth adapter disappeared during connect: {error}"
            )

        if attempted_pairing:
            _LOGGER.error("Pairing with the stove failed: %s", error)
            return BLENotPairedError(
                f"Not paired with {self._mac_address}, and pairing failed: {error}"
            )

        if self._disconnected_during_connect:
            if self._last_disconnect_reason is None and self._bluez is not None:
                # The D-Bus signal can trail bleak's callback slightly.
                await asyncio.sleep(0.5)
            reason = (
                REASON_AUTHENTICATION
                if self._auth_rejected.is_set()
                else self._last_disconnect_reason
            )
            _LOGGER.warning(
                "Stove dropped the connection during setup (BlueZ reason: %s)",
                reason or "not reported",
            )
            return BLEHandshakeDropError(
                f"Stove dropped the connection during setup: {error}", reason
            )

        if isinstance(error, BleakError):
            _LOGGER.error("Failed to connect: %s", error)
        else:
            _LOGGER.error("Unexpected error during connect: %s", error)
        return BLEConnectionError(f"Failed to connect to {self._mac_address}: {error}")

    async def pair(self, duration: float = PAIRING_WINDOW) -> None:
        """Forget any existing bond and pair with the stove afresh.

        Watches for the stove for up to ``duration`` seconds and pairs as soon
        as it is seen, retrying until it accepts. Power-cycling the stove
        meanwhile may help if it refuses. On success the client is left
        connected.

        Raises:
            BLEAdapterUnavailableError: No powered Bluetooth adapter.
            BLENotPairedError: The window passed without a successful pairing.
            BLEConnectionError: Not supported on this platform.
        """
        if self._bluez is None:
            raise BLEConnectionError("Pairing mode is only supported on Linux/BlueZ")

        async with self._connection_lock:
            await self._teardown_client()

            state = await self._bluez_state()
            if not state.adapter_present:
                raise BLEAdapterUnavailableError("No powered Bluetooth adapter found")

            if state.device_path is not None:
                _LOGGER.warning(
                    "Removing the existing BlueZ record (and bond) for %s",
                    self._mac_address,
                )
                if state.connected:
                    await self._bluez.disconnect_device(state)
                await self._bluez.remove_device(state)

            _LOGGER.warning(
                "PAIRING MODE for %ds (power-cycle the stove if it does not pair)",
                int(duration),
            )

            async with self._pairing_agent():
                await self._pair_until(duration)

    async def _pair_until(self, duration: float) -> None:
        """Retry pairing until it succeeds or ``duration`` seconds pass."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration
        attempts = 0
        last_error: Optional[Exception] = None

        while loop.time() < deadline:
            device = await self._find_device(PAIRING_SCAN_TIMEOUT, quiet=True)
            if device is None:
                continue

            # bleak skips Pair (and Connect) when BlueZ already reports the
            # device as connected, and BlueZ keeps re-establishing links of its
            # own after failed attempts. Start each attempt from a clean slate.
            state = await self._bluez_state()
            if state is not None and state.connected:
                await self._bluez.disconnect_device(state)
                await asyncio.sleep(1)

            await self._apply_conn_params(state)

            attempts += 1
            _LOGGER.info("Stove visible, pairing attempt %d...", attempts)
            try:
                await self._open(device, pair=True, timeout=PAIRING_CONNECT_TIMEOUT)
            except Exception as e:
                last_error = e
                _LOGGER.info("Pairing attempt %d failed: %s", attempts, e)
                await self._close_failed_client()
                await asyncio.sleep(PAIRING_RETRY_DELAY)
                continue

            state = await self._bluez_state()
            if state is not None and not state.bonded:
                _LOGGER.warning(
                    "Paired, but the stove did not agree to bonding; the "
                    "pairing will not survive a Bluetooth restart"
                )
            _LOGGER.info("Pairing successful after %d attempt(s)", attempts)
            return

        raise BLENotPairedError(
            f"Pairing window closed after {attempts} attempt(s)"
            + (f": {last_error}" if last_error else " (stove never seen)")
        )

    def _on_disconnect(self, client: BleakClient) -> None:
        """Handle disconnection event."""
        if client is not self._client:
            # A client we have already replaced or torn down. Acting on this
            # would corrupt the state of the connection we actually care about.
            _LOGGER.debug("Ignoring disconnect from a superseded BLE client")
            return

        _LOGGER.warning("Disconnected from stove")
        if self._connect_in_progress:
            self._disconnected_during_connect = True
        self._connected = False
        self._drain_response_queue()

    async def _teardown_client(self) -> None:
        """Detach and disconnect the current BleakClient, if any."""
        client = self._client
        # Detach first so the resulting disconnect callback is ignored.
        self._client = None
        self._connected = False
        self._connect_in_progress = False

        if client is None:
            return

        _LOGGER.debug("Cleaning up previous BLE client...")
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception as e:
            _LOGGER.debug("Error tearing down previous BLE client: %s", e)

    async def _find_device(self, timeout: float, quiet: bool = False) -> Optional[BLEDevice]:
        """Look up a fresh BLEDevice for the stove.

        Uses a targeted lookup rather than a full ``discover()`` sweep: an
        active scan of the whole neighbourhood competes with Home Assistant's
        own Bluetooth integration on the same adapter.
        """
        try:
            device = await BleakScanner.find_device_by_address(
                self._mac_address, timeout=timeout
            )
        except Exception as scan_err:
            _LOGGER.warning("Scan failed: %s", scan_err)
            if quiet:
                # Do not spin on an instant failure inside the pairing loop.
                await asyncio.sleep(PAIRING_RETRY_DELAY)
            return None

        if device:
            _LOGGER.info("Found device via scan: %s (%s)", device.name, device.address)
        elif not quiet:
            _LOGGER.info("Stove not seen in scan, trying a direct connection")
        return device

    async def disconnect(self) -> None:
        """Disconnect from the stove."""
        async with self._connection_lock:
            client = self._client
            if client is None:
                self._connected = False
                return

            # Detach first so the disconnect callback is a no-op.
            self._client = None
            self._connected = False
            self._connect_in_progress = False

            try:
                if client.is_connected:
                    try:
                        await client.stop_notify(BLE_NOTIFY_CHAR_UUID)
                    except Exception as e:
                        _LOGGER.debug("stop_notify failed during disconnect: %s", e)
                    await client.disconnect()
            except BleakError as e:
                _LOGGER.warning("Error during disconnect: %s", e)
            finally:
                self._drain_response_queue()
                _LOGGER.info("Disconnected from stove")

    def close(self) -> None:
        """Release the BlueZ D-Bus connection."""
        if self._bluez is not None:
            self._bluez.close()

    async def send_command(self, command: bytes) -> bytes:
        """Send command and wait for response.

        Only one exchange runs at a time; callers queue behind the command
        lock.

        Args:
            command: 32-byte encrypted command packet.

        Returns:
            32-byte encrypted response.

        Raises:
            BLEConnectionError: If not connected.
            BLECommandError: If command fails or times out.
        """
        async with self._command_lock:
            if not self.connected:
                raise BLEConnectionError("Not connected to stove")

            client = self._client
            self._drain_response_queue()

            try:
                _LOGGER.debug("Sending command: %d bytes", len(command))

                # ABF1 is write-without-response only, so response=False is
                # required here rather than a tuning choice.
                await client.write_gatt_char(
                    BLE_WRITE_CHAR_UUID,
                    command,
                    response=False,
                )

                try:
                    response = await asyncio.wait_for(
                        self._response_queue.get(),
                        timeout=self._command_timeout,
                    )
                    _LOGGER.debug("Received response: %d bytes", len(response))
                    return response

                except asyncio.TimeoutError:
                    # Mark the connection broken so the main loop reconnects on
                    # the next cycle rather than retrying against a stuck link.
                    _LOGGER.warning("Command timeout — marking connection as broken")
                    self._connected = False
                    raise BLECommandError(
                        f"Command timeout after {self._command_timeout}s"
                    ) from None

            except BleakError as e:
                _LOGGER.error("Command failed: %s", e)
                self._connected = False
                raise BLECommandError(f"Command failed: {e}") from e


async def discover_edilkamin_devices(timeout: float = 10.0) -> list[BLEDevice]:
    """Discover Edilkamin devices via BLE scan.

    Args:
        timeout: Scan timeout in seconds.

    Returns:
        List of discovered Edilkamin BLE devices.
    """
    _LOGGER.info("Scanning for Edilkamin devices...")

    devices = await BleakScanner.discover(timeout=timeout)

    edilkamin_devices = [
        d for d in devices if d.name and d.name.startswith(DEVICE_NAME_PREFIX)
    ]

    for device in edilkamin_devices:
        _LOGGER.info("Found: %s (%s)", device.name, device.address)

    return edilkamin_devices
