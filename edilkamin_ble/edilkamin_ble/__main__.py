"""Main entry point for Edilkamin BLE add-on."""

import asyncio
import logging
import signal
import sys
from typing import Any, Awaitable, Optional

from .ble_client import (
    BLEAdapterUnavailableError,
    BLEConnectionError,
    BLEHandshakeDropError,
    BLENotPairedError,
    EdilkaminBLEClient,
)
from .config import Config
from .const import (
    AUTO_REPAIR_COOLDOWN,
    AVAILABILITY_GRACE,
    AUTO_REPAIR_WINDOW,
    BT_STATUS_CONNECTED,
    BT_STATUS_CONNECTING,
    BT_STATUS_DISCONNECTED,
    BT_STATUS_NO_ADAPTER,
    BT_STATUS_NOT_PAIRED,
    BT_STATUS_PAIRING,
    BT_STATUS_PAIRING_FAILED,
    BT_STATUS_PAIRING_REQUIRED,
    DEFAULT_RECONNECT_DELAY,
    HANDSHAKE_DROPS_BEFORE_REPAIR_HINT,
    MAX_BACKOFF,
    PAIRING_WINDOW,
)
from .mqtt_discovery import DeviceInfo
from .mqtt_handler import CommandHandler, MQTTHandler
from .stove import EdilkaminStove

_LOGGER = logging.getLogger(__name__)

# A missing adapter is usually fixed by re-attaching hardware, after which the
# add-on should notice quickly rather than sit out a five-minute backoff.
MAX_ADAPTER_BACKOFF = 60

PAIRING_HINT = (
    "To pair: press 'Pair Stove' in Home Assistant (or publish PRESS to "
    "edilkamin/<device>/pair/set). If it still fails, power-cycle the stove "
    "within %d seconds of pressing it, and close the Edilkamin phone app."
    % int(PAIRING_WINDOW)
)


class EdilkaminAddon:
    """Main add-on orchestrator."""

    def __init__(self, config: Config):
        """Initialize the add-on.

        Args:
            config: Add-on configuration.
        """
        self._config = config
        self._running = False
        self._stopped = False
        self._stop_event = asyncio.Event()
        self._refresh_event = asyncio.Event()
        self._pair_event = asyncio.Event()
        self._bt_status: Optional[str] = None
        self._last_failed_auto_repair: Optional[float] = None
        # When the stove link was last lost, and whether that has been
        # published yet; see _stove_unreachable().
        self._unreachable_since: Optional[float] = None
        self._reported_offline = False

        # Components
        self._ble_client: Optional[EdilkaminBLEClient] = None
        self._stove: Optional[EdilkaminStove] = None
        self._mqtt: Optional[MQTTHandler] = None
        self._command_handler: Optional[CommandHandler] = None

    async def start(self) -> None:
        """Start the add-on."""
        _LOGGER.info("Starting Edilkamin BLE Add-on")
        _LOGGER.info("BLE MAC: %s", self._config.ble_mac_address)
        _LOGGER.info("MQTT: %s:%d", self._config.mqtt_host, self._config.mqtt_port)

        self._running = True

        # Initialize BLE client
        self._ble_client = EdilkaminBLEClient(self._config.ble_mac_address)
        self._stove = EdilkaminStove(self._ble_client)

        # Initialize MQTT
        device_info = DeviceInfo(
            identifier=self._config.device_id,
            name=self._config.device_name,
            model="Pellet stove (The Mind)",
        )

        self._mqtt = MQTTHandler(
            host=self._config.mqtt_host,
            port=self._config.mqtt_port,
            username=self._config.mqtt_username,
            password=self._config.mqtt_password,
            client_id=f"edilkamin-ble-{self._config.device_id}",
        )
        self._mqtt.setup_discovery(device_info, self._config.mqtt_discovery_prefix)

        # Register the command handler before subscribing, so commands that
        # arrive the instant we connect are not dropped on the floor.
        self._command_handler = CommandHandler(
            self._stove, self._mqtt, self._refresh_event, self._pair_event
        )

        # Connect to MQTT. paho retries in the background, so keep waiting
        # rather than exiting — the broker is often not up yet at boot.
        attempt = 0
        while self._running and not await self._mqtt.connect():
            attempt += 1
            delay = self._backoff(attempt)
            _LOGGER.error(
                "Could not reach the MQTT broker (attempt %d), retrying in %ds",
                attempt,
                delay,
            )
            await self._sleep(delay)

        if not self._running:
            return

        # Publish discovery configs
        await self._mqtt.publish_discovery()
        await self._set_bt_status(BT_STATUS_CONNECTING)

        # Start main loop
        await self._main_loop()

    @staticmethod
    def _backoff(failures: int, cap: int = MAX_BACKOFF) -> int:
        """Return the retry delay for the Nth consecutive failure."""
        return int(min(DEFAULT_RECONNECT_DELAY * 2 ** (failures - 1), cap))

    async def _wait_any(self, timeout: float, *events: asyncio.Event) -> None:
        """Wait until any of ``events`` is set, or ``timeout`` passes."""
        waiters = [asyncio.ensure_future(event.wait()) for event in events]
        try:
            await asyncio.wait(
                waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters:
                waiter.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

    async def _sleep(self, delay: float) -> None:
        """Back off, waking early for shutdown or a pairing request."""
        await self._wait_any(delay, self._stop_event, self._pair_event)

    async def _wait_next_poll(self) -> None:
        """Wait for the poll interval, a command refresh, pairing, or shutdown."""
        await self._wait_any(
            self._config.poll_interval,
            self._refresh_event,
            self._stop_event,
            self._pair_event,
        )

    async def _until_stopped(self, awaitable: Awaitable[Any]) -> None:
        """Run ``awaitable``, cancelling it if the add-on is shutting down.

        Pairing mode runs for minutes; without this a shutdown would wait for
        it and the Supervisor would SIGKILL us mid-pairing.
        """
        task = asyncio.ensure_future(awaitable)
        stop_waiter = asyncio.ensure_future(self._stop_event.wait())
        try:
            await asyncio.wait(
                {task, stop_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            stop_waiter.cancel()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if not task.cancelled():
            task.result()

    async def _set_bt_status(self, status: str) -> None:
        """Publish the Bluetooth Status sensor when it changes."""
        if status == self._bt_status:
            return
        self._bt_status = status
        await self._mqtt.publish_bluetooth_status(status)

    async def _stove_unreachable(self) -> None:
        """Note that the stove is unreachable; report it only if it lasts.

        A reconnect usually takes a few seconds. Marking every entity
        unavailable for each one filled Home Assistant's logbook and history
        with "became unavailable" between almost every real change. Until
        AVAILABILITY_GRACE has passed, entities keep their last values.
        """
        now = asyncio.get_running_loop().time()
        if self._unreachable_since is None:
            self._unreachable_since = now
        if (
            not self._reported_offline
            and now - self._unreachable_since >= AVAILABILITY_GRACE
        ):
            _LOGGER.warning(
                "Stove unreachable for %d s, marking entities unavailable",
                int(now - self._unreachable_since),
            )
            await self._mqtt.publish_availability(False)
            self._reported_offline = True

    def _auto_repair_allowed(self) -> bool:
        """Back off only after an automatic re-pair *failed*.

        The stove discards keys often (minutes after pairing has been seen),
        and a re-pair takes a few seconds, so a successful one is no reason
        to wait. A failing one is: retrying it endlessly would keep the add-on
        in pairing mode.
        """
        if self._last_failed_auto_repair is None:
            return True
        elapsed = asyncio.get_running_loop().time() - self._last_failed_auto_repair
        return elapsed >= AUTO_REPAIR_COOLDOWN

    async def _run_pairing_mode(self, duration: float = PAIRING_WINDOW) -> bool:
        """Forget the stove's bond and pair afresh. Returns True on success."""
        _LOGGER.warning("Pairing requested")
        await self._stove_unreachable()
        await self._set_bt_status(BT_STATUS_PAIRING)

        try:
            await self._until_stopped(self._ble_client.pair(duration))
        except BLEAdapterUnavailableError as e:
            _LOGGER.error("Cannot pair: %s", e)
            await self._set_bt_status(BT_STATUS_NO_ADAPTER)
        except BLEConnectionError as e:
            _LOGGER.error("Pairing failed: %s. %s", e, PAIRING_HINT)
            await self._set_bt_status(BT_STATUS_PAIRING_FAILED)
        else:
            if self._ble_client.connected:
                await self._set_bt_status(BT_STATUS_CONNECTED)
                return True
        return False

    async def _main_loop(self) -> None:
        """Main polling and reconnection loop."""
        consecutive_failures = 0
        handshake_drops = 0

        while self._running:
            try:
                if self._pair_event.is_set():
                    self._pair_event.clear()
                    await self._run_pairing_mode()
                    consecutive_failures = 0
                    handshake_drops = 0
                    continue

                # Ensure BLE connection
                if not self._ble_client.connected:
                    _LOGGER.info("Connecting to stove...")
                    await self._stove_unreachable()

                    try:
                        await self._ble_client.connect()
                    except BLEAdapterUnavailableError as e:
                        consecutive_failures += 1
                        delay = self._backoff(consecutive_failures, MAX_ADAPTER_BACKOFF)
                        _LOGGER.error(
                            "No Bluetooth adapter (attempt %d): %s — retrying in %ds",
                            consecutive_failures,
                            e,
                            delay,
                        )
                        await self._set_bt_status(BT_STATUS_NO_ADAPTER)
                        await self._sleep(delay)
                        continue
                    except BLENotPairedError as e:
                        consecutive_failures += 1
                        delay = self._backoff(consecutive_failures)
                        _LOGGER.warning("%s. %s", e, PAIRING_HINT)
                        await self._set_bt_status(BT_STATUS_NOT_PAIRED)
                        await self._sleep(delay)
                        continue
                    except BLEHandshakeDropError as e:
                        consecutive_failures += 1
                        handshake_drops += 1
                        delay = self._backoff(consecutive_failures)
                        suspected = (
                            e.authentication_failure
                            or handshake_drops >= HANDSHAKE_DROPS_BEFORE_REPAIR_HINT
                        )
                        if suspected and self._auto_repair_allowed():
                            # Either BlueZ said so (reason Authentication: the
                            # stove answered our key with "PIN or Key Missing"),
                            # or the bonded link keeps failing during setup
                            # while the adapter is fine. On some adapters the
                            # key rejection arrives before BlueZ considers the
                            # link connected, and then it reports no reason.
                            # A wrong guess is cheap: with the pairing agent,
                            # re-pairing needs no user action, and an unpaired
                            # connect pairs by itself once the link is back.
                            _LOGGER.warning(
                                "The stove is rejecting our pairing (%d in a "
                                "row, BlueZ reason: %s). Re-pairing automatically.",
                                handshake_drops,
                                e.reason or "not reported",
                            )
                            if await self._run_pairing_mode(AUTO_REPAIR_WINDOW):
                                self._last_failed_auto_repair = None
                            else:
                                self._last_failed_auto_repair = (
                                    asyncio.get_running_loop().time()
                                )
                            consecutive_failures = 0
                            handshake_drops = 0
                            continue
                        if suspected:
                            _LOGGER.warning(
                                "The stove keeps rejecting the connection "
                                "(%d in a row, BlueZ reason: %s), and automatic "
                                "re-pairing failed less than %d minutes ago. %s",
                                handshake_drops,
                                e.reason or "not reported",
                                int(AUTO_REPAIR_COOLDOWN // 60),
                                PAIRING_HINT,
                            )
                            await self._set_bt_status(BT_STATUS_PAIRING_REQUIRED)
                        else:
                            await self._set_bt_status(BT_STATUS_DISCONNECTED)
                        _LOGGER.error("Connection failed: %s — retrying in %ds", e, delay)
                        await self._sleep(delay)
                        continue
                    except BLEConnectionError as e:
                        consecutive_failures += 1
                        delay = self._backoff(consecutive_failures)
                        _LOGGER.error(
                            "Connection failed (attempt %d): %s — retrying in %ds",
                            consecutive_failures,
                            e,
                            delay,
                        )
                        await self._set_bt_status(BT_STATUS_DISCONNECTED)
                        await self._sleep(delay)
                        continue

                    consecutive_failures = 0
                    handshake_drops = 0
                    _LOGGER.info("Connected to stove")
                    await self._set_bt_status(BT_STATUS_CONNECTED)

                # Clear before polling, so a command that lands mid-poll still
                # triggers another refresh afterwards.
                self._refresh_event.clear()

                try:
                    state = await self._stove.poll_status()
                except Exception as e:
                    consecutive_failures += 1
                    delay = self._backoff(consecutive_failures)
                    _LOGGER.error(
                        "Failed to poll status (attempt %d): %s — reconnecting in %ds",
                        consecutive_failures,
                        e,
                        delay,
                    )
                    await self._stove_unreachable()
                    await self._set_bt_status(BT_STATUS_DISCONNECTED)
                    # Drop the link so the top of the loop rebuilds it cleanly.
                    await self._ble_client.disconnect()
                    await self._sleep(delay)
                    continue

                await self._mqtt.publish_state(state)
                # Republish availability on every successful poll. Doing it only
                # on connect meant that any recovery which did not go through
                # the connect branch left the entities stuck "unavailable".
                await self._mqtt.publish_availability(True)
                self._unreachable_since = None
                self._reported_offline = False
                await self._set_bt_status(BT_STATUS_CONNECTED)
                consecutive_failures = 0

                await self._wait_next_poll()

            except asyncio.CancelledError:
                _LOGGER.info("Main loop cancelled")
                break
            except Exception as e:
                _LOGGER.exception("Unexpected error in main loop: %s", e)
                await self._sleep(self._config.poll_interval)

    def request_stop(self) -> None:
        """Ask the add-on to wind down.

        Safe to call from a signal handler: it only flips flags, so every
        in-flight sleep wakes up and the loops unwind on their own.
        """
        self._running = False
        self._stop_event.set()

    async def stop(self) -> None:
        """Stop the add-on."""
        if self._stopped:
            return
        self._stopped = True

        _LOGGER.info("Stopping Edilkamin BLE Add-on")
        self._running = False
        self._stop_event.set()

        # Publish offline status
        if self._mqtt:
            await self._mqtt.publish_availability(False)
            await self._mqtt.disconnect()

        # Disconnect BLE
        if self._ble_client:
            await self._ble_client.disconnect()
            self._ble_client.close()

        _LOGGER.info("Add-on stopped")


async def main() -> int:
    """Main entry point."""
    # Load configuration
    config = Config.load()
    config.setup_logging()

    # Validate configuration
    errors = config.validate()
    if errors:
        for error in errors:
            _LOGGER.error("Configuration error: %s", error)
        return 1

    # Create and start add-on
    addon = EdilkaminAddon(config)

    # Setup signal handlers
    loop = asyncio.get_running_loop()

    def signal_handler() -> None:
        _LOGGER.info("Received shutdown signal")
        # Only signal here; the actual teardown runs in the finally block
        # below, once start() has unwound.
        addon.request_stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await addon.start()
    except KeyboardInterrupt:
        _LOGGER.info("Keyboard interrupt received")
    finally:
        await addon.stop()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
