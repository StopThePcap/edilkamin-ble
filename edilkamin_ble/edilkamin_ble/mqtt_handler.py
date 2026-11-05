"""MQTT handler for state publishing and command handling."""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Optional

import paho.mqtt.client as mqtt

from .const import (
    ALARM_DESCRIPTIONS,
    COMMAND_SETTLE_DELAY,
    MQTT_CONNECT_TIMEOUT,
    MQTT_DISCOVERY_PREFIX,
    PHASE_LABELS,
)
from .mqtt_discovery import DeviceInfo, MQTTDiscovery
from .stove import EdilkaminStove, StovePhase, StoveState

_LOGGER = logging.getLogger(__name__)


# Fan mode mapping
FAN_MODE_TO_VALUE = {
    "Auto": 6,
    "20%": 1,
    "40%": 2,
    "60%": 3,
    "80%": 4,
    "100%": 5,
}

FAN_VALUE_TO_MODE = {v: k for k, v in FAN_MODE_TO_VALUE.items()}

# Preset mapping
PRESET_TO_LEVEL = {
    "Auto": 0,  # Auto mode
    "P1": 1,
    "P2": 2,
    "P3": 3,
    "P4": 4,
    "P5": 5,
}

LEVEL_TO_PRESET = {v: k for k, v in PRESET_TO_LEVEL.items()}


class MQTTHandler:
    """Handles MQTT communication with Home Assistant."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: Optional[str] = None,
    ):
        """Initialize MQTT handler.

        Args:
            host: MQTT broker host.
            port: MQTT broker port.
            username: Optional MQTT username.
            password: Optional MQTT password.
            client_id: Optional stable MQTT client identifier.
        """
        self._host = host
        self._port = port
        self._username = username
        self._password = password

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or "",
        )
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._connected = False
        self._loop_started = False
        self._has_connected_before = False
        self._bt_status: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Discovery
        self._device_info: Optional[DeviceInfo] = None
        self._discovery: Optional[MQTTDiscovery] = None
        self._topics: dict[str, str] = {}
        self._command_topics: dict[str, str] = {}

        # Command callback
        self._command_callback: Optional[Callable[[str, str], Coroutine[Any, Any, None]]] = None

        # Setup callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        if username and password:
            self._client.username_pw_set(username, password)

    @property
    def connected(self) -> bool:
        """Return True if connected to MQTT broker."""
        return self._connected

    def set_command_callback(
        self,
        callback: Callable[[str, str], Coroutine[Any, Any, None]],
    ) -> None:
        """Set callback for incoming commands.

        Args:
            callback: Async function taking (command_name, payload).
        """
        self._command_callback = callback

    def _on_connect(self, client: mqtt.Client, userdata: Any, *args) -> None:
        """Handle MQTT connection.

        Args vary by paho-mqtt version:
        - v1.x: (client, userdata, flags, rc)
        - v2.x: (client, userdata, flags, reason_code, properties)
        """
        # Extract return code - it's the 4th arg (index 1 in *args after flags)
        rc = args[1] if len(args) > 1 else 0
        # Handle both int (v1) and ReasonCode object (v2)
        rc_value = int(rc) if hasattr(rc, '__int__') else (rc.value if hasattr(rc, 'value') else rc)

        if rc_value == 0:
            _LOGGER.info("Connected to MQTT broker")
            self._connected = True

            # paho re-sends the Last Will's "offline" whenever the connection
            # drops uncleanly, so announce ourselves again on every connect.
            self._publish_bridge_sync(True)
            if self._bt_status is not None:
                # Anything published while the broker was away was dropped.
                self._publish("bluetooth_status", self._bt_status)

            # Subscribe to command topics
            for name, topic in self._command_topics.items():
                client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)

            if self._has_connected_before:
                # A broker restart drops retained discovery messages, which
                # would leave Home Assistant without any entities. paho
                # publishes are thread safe, so this is fine from the callback.
                _LOGGER.info("Reconnected to MQTT broker, re-publishing discovery")
                self._publish_discovery_sync()
            self._has_connected_before = True
        else:
            _LOGGER.error("Failed to connect to MQTT broker: %s", rc_value)
            # 4/5 (MQTT 3.1.1) and 134/135 (MQTT 5): bad credentials or not
            # authorised. By far the most common setup problem, so say how to
            # fix it.
            if rc_value in (4, 5, 134, 135):
                _LOGGER.error(
                    "The MQTT broker rejected the login. With the Mosquitto "
                    "add-on, leave MQTT username and password empty so the "
                    "add-on gets credentials from the Supervisor automatically. "
                    "Otherwise enter a valid broker user (e.g. a Home Assistant "
                    "user for Mosquitto)."
                )
            self._connected = False

    def _on_disconnect(self, client: mqtt.Client, userdata: Any, *args) -> None:
        """Handle MQTT disconnection.

        Args vary by paho-mqtt version:
        - v1.x: (client, userdata, rc)
        - v2.x: (client, userdata, disconnect_flags, reason_code, properties)
        """
        # Extract reason code from args if available
        reason = args[1] if len(args) > 1 else (args[0] if args else "unknown")
        _LOGGER.warning("Disconnected from MQTT broker: %s", reason)
        self._connected = False

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        msg: mqtt.MQTTMessage,
    ) -> None:
        """Handle incoming MQTT message."""
        topic = msg.topic
        payload = msg.payload.decode("utf-8")

        _LOGGER.debug("Received message on %s: %s", topic, payload)

        # Find which command this is
        for name, cmd_topic in self._command_topics.items():
            if topic == cmd_topic:
                if msg.retain:
                    # A retained command is an old one replayed by the broker
                    # on subscribe, not something the user just did. If a
                    # client ever published commands with retain set, the
                    # broker keeps e.g. "power OFF" and would switch the stove
                    # off on every add-on start. Drop it and clear it from the
                    # broker.
                    if msg.payload:
                        _LOGGER.warning(
                            "Ignoring and clearing retained command %s = %s",
                            name,
                            payload,
                        )
                        client.publish(topic, None, retain=True)
                    break
                if not msg.payload:
                    # Clearing a retained message delivers an empty one to us.
                    # It is not a command; "" would otherwise read as OFF.
                    break

                if self._command_callback and self._loop:
                    asyncio.run_coroutine_threadsafe(
                        self._command_callback(name, payload),
                        self._loop,
                    )
                break

    async def connect(self, timeout: float = MQTT_CONNECT_TIMEOUT) -> bool:
        """Connect to MQTT broker.

        Safe to call repeatedly: the network loop is started once and paho
        keeps retrying in the background, so a later call simply waits for that
        retry to succeed.

        Args:
            timeout: Seconds to wait for the connection to come up.

        Returns:
            True if connection successful.
        """
        self._loop = asyncio.get_running_loop()

        try:
            if not self._loop_started:
                bridge_topic = self._topics.get("bridge")
                if bridge_topic:
                    # Without a will, a crash leaves a retained "online" behind
                    # and Home Assistant shows stale values indefinitely. Every
                    # entity depends on the bridge topic, so this greys out the
                    # whole device.
                    self._client.will_set(bridge_topic, "offline", retain=True)

                _LOGGER.info(
                    "Connecting to MQTT broker at %s:%d", self._host, self._port
                )
                self._client.connect_async(self._host, self._port)
                self._client.loop_start()
                self._loop_started = True

            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if self._connected:
                    return True
                await asyncio.sleep(0.1)

            _LOGGER.error("MQTT connection timeout after %ss", timeout)
            return False

        except Exception as e:
            _LOGGER.error("Failed to connect to MQTT: %s", e)
            return False

    async def disconnect(self) -> None:
        """Disconnect from MQTT broker."""
        # A clean disconnect does not trigger the Last Will.
        self._publish_bridge_sync(False)
        self._client.disconnect()
        self._client.loop_stop()
        self._loop_started = False
        self._connected = False
        _LOGGER.info("Disconnected from MQTT broker")

    def setup_discovery(
        self,
        device_info: DeviceInfo,
        discovery_prefix: str = MQTT_DISCOVERY_PREFIX,
    ) -> None:
        """Setup MQTT discovery for a device.

        Args:
            device_info: Device information.
            discovery_prefix: Home Assistant discovery prefix.
        """
        self._device_info = device_info
        self._discovery = MQTTDiscovery(device_info, discovery_prefix=discovery_prefix)
        self._topics = self._discovery.get_state_topics()
        self._command_topics = self._discovery.get_command_topics()

    def _publish_discovery_sync(self) -> None:
        """Publish all discovery configurations (callable from any thread)."""
        if not self._discovery:
            _LOGGER.error("Discovery not setup")
            return

        configs = self._discovery.get_all_discovery_configs()

        for topic, payload in configs:
            self._client.publish(topic, json.dumps(payload), retain=True)
            _LOGGER.debug("Published discovery to %s", topic)

        _LOGGER.info("Published %d discovery configs", len(configs))

    async def publish_discovery(self) -> None:
        """Publish all discovery configurations."""
        self._publish_discovery_sync()

    async def publish_availability(self, available: bool) -> None:
        """Publish availability status.

        Args:
            available: True if device is available.
        """
        topic = self._topics.get("availability")
        if topic:
            self._publish_raw(topic, "online" if available else "offline")

    def _publish_bridge_sync(self, online: bool) -> None:
        """Publish add-on availability (callable from any thread)."""
        topic = self._topics.get("bridge")
        if topic:
            self._publish_raw(topic, "online" if online else "offline")

    async def publish_bluetooth_status(self, status: str) -> None:
        """Publish the Bluetooth link / pairing status.

        Args:
            status: One of the BT_STATUS_* values in const.py.
        """
        self._bt_status = status
        self._publish("bluetooth_status", status)

    async def publish_state(self, state: StoveState) -> None:
        """Publish full stove state.

        Args:
            state: Current stove state.
        """
        # Climate mode
        mode = "heat" if state.power_on else "off"
        self._publish("mode", mode)

        # Preset (power level or auto)
        if state.automatic_mode:
            preset = "Auto"
        else:
            preset = LEVEL_TO_PRESET.get(state.power_level, f"P{state.power_level}")
        self._publish("preset", preset)

        # Fan mode
        fan_mode = FAN_VALUE_TO_MODE.get(state.fan_speed, "Auto")
        self._publish("fan_mode", fan_mode)

        # Temperatures
        self._publish("current_temperature", f"{state.current_temperature:.1f}")
        self._publish("target_temperature", f"{state.target_temperature:.1f}")
        self._publish("exhaust_temperature", f"{state.exhaust_temperature:.1f}")

        # Action (what the climate is currently doing)
        action = self._get_action(state)
        self._publish("action", action)

        # Status
        self._publish("phase", PHASE_LABELS.get(state.phase.value, "Unknown"))
        sub_phase, phase_code = state.status_bytes
        self._publish("phase_attributes", json.dumps({
            "phase_code": phase_code,
            "sub_phase_code": sub_phase,
        }))
        self._publish("power_level", f"P{state.power_level}")
        self._publish("fan_speed", FAN_VALUE_TO_MODE.get(state.fan_speed, str(state.fan_speed)))

        # Binary sensors
        self._publish("running", "ON" if state.power_on else "OFF")
        self._publish("alarm", "ON" if state.alarm else "OFF")
        self._publish("alarm_attributes", json.dumps({
            "code": state.alarm_code,
            "description": ALARM_DESCRIPTIONS.get(
                state.alarm_code, f"Unknown alarm {state.alarm_code}"
            ),
        }))
        self._publish("pellet_empty", "ON" if state.pellet_level == "empty" else "OFF")

        # Switches
        self._publish("power", "ON" if state.power_on else "OFF")
        self._publish("relax", "ON" if state.relax_mode else "OFF")
        self._publish("chrono", "ON" if state.chrono_mode else "OFF")
        self._publish("standby", "ON" if state.standby_mode else "OFF")
        self._publish("automatic", "ON" if state.automatic_mode else "OFF")

    def _publish(self, state_name: str, value: str) -> None:
        """Publish a state value.

        Args:
            state_name: Name of the state.
            value: Value to publish.
        """
        topic = self._topics.get(state_name)
        if topic:
            self._publish_raw(topic, value)

    def _publish_raw(self, topic: str, value: str) -> None:
        """Publish to a topic, logging failures instead of dropping them."""
        info = self._client.publish(topic, value, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            _LOGGER.warning("Failed to publish to %s: rc=%s", topic, info.rc)

    def _get_action(self, state: StoveState) -> str:
        """Get climate action from stove state."""
        if not state.power_on:
            return "off"

        if state.phase == StovePhase.RUNNING:
            return "heating"
        elif state.phase.is_ignition:
            return "preheating"
        else:
            return "idle"


class CommandHandler:
    """Handles MQTT commands and translates them to stove actions."""

    def __init__(
        self,
        stove: EdilkaminStove,
        mqtt_handler: MQTTHandler,
        refresh_event: Optional[asyncio.Event] = None,
        pair_event: Optional[asyncio.Event] = None,
    ):
        """Initialize command handler.

        Args:
            stove: Stove controller.
            mqtt_handler: MQTT handler.
            refresh_event: Set after a command so the poll loop re-reads the
                stove. Several commands arriving together collapse into a
                single poll instead of one full 12-register sweep each.
            pair_event: Set when the user presses "Pair Stove". The main loop
                owns the BLE link, so it runs pairing mode itself.
        """
        self._stove = stove
        self._mqtt = mqtt_handler
        self._refresh_event = refresh_event
        self._pair_event = pair_event
        self._mqtt.set_command_callback(self._handle_command)

    async def _handle_command(self, command: str, payload: str) -> None:
        """Handle incoming command.

        Args:
            command: Command name.
            payload: Command payload.
        """
        _LOGGER.info("Handling command: %s = %s", command, payload)

        if command == "pair":
            if self._pair_event is not None:
                self._pair_event.set()
            return

        try:
            if command == "mode":
                await self._handle_mode(payload)
            elif command == "preset":
                await self._handle_preset(payload)
            elif command == "fan_mode":
                await self._handle_fan_mode(payload)
            elif command == "target_temperature":
                await self._handle_temperature(payload)
            elif command == "power":
                await self._handle_switch(payload, self._stove.set_power)
            elif command == "relax":
                await self._handle_switch(payload, self._stove.set_relax_mode)
            elif command == "chrono":
                await self._handle_switch(payload, self._stove.set_chrono_mode)
            elif command == "standby":
                await self._handle_switch(payload, self._stove.set_standby_mode)
            elif command == "automatic":
                await self._handle_switch(payload, self._stove.set_automatic_mode)
            else:
                _LOGGER.warning("Unknown command: %s", command)
                return

        except Exception as e:
            _LOGGER.error("Failed to handle command %s: %s", command, e)

        # Ask the poll loop for a refresh rather than polling here: polling from
        # the command path meant every simultaneous command started its own full
        # sweep of the stove over a link that handles one request at a time.
        if self._refresh_event is not None:
            await asyncio.sleep(COMMAND_SETTLE_DELAY)
            self._refresh_event.set()

    async def _handle_mode(self, payload: str) -> None:
        """Handle climate mode command."""
        if payload.lower() == "heat":
            await self._stove.set_power(True)
        elif payload.lower() == "off":
            await self._stove.set_power(False)

    async def _handle_preset(self, payload: str) -> None:
        """Handle preset mode command."""
        if payload == "Auto":
            await self._stove.set_automatic_mode(True)
        else:
            level = PRESET_TO_LEVEL.get(payload, 1)
            if level > 0:
                await self._stove.set_automatic_mode(False)
                await self._stove.set_power_level(level)

    async def _handle_fan_mode(self, payload: str) -> None:
        """Handle fan mode command."""
        speed = FAN_MODE_TO_VALUE.get(payload, 6)
        await self._stove.set_fan_speed(speed)

    async def _handle_temperature(self, payload: str) -> None:
        """Handle temperature command."""
        try:
            temp = float(payload)
            await self._stove.set_temperature(temp)
        except ValueError:
            _LOGGER.error("Invalid temperature value: %s", payload)

    async def _handle_switch(
        self,
        payload: str,
        handler: Callable[[bool], Coroutine[Any, Any, bool]],
    ) -> None:
        """Handle switch command."""
        enabled = payload.upper() == "ON"
        await handler(enabled)
