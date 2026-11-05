"""Direct BlueZ D-Bus access for adapter, bond and disconnect-reason state.

bleak covers GATT, but it does not expose whether an adapter exists, whether
a device is bonded, or *why* BlueZ dropped a link. Pairing decisions need all
three, and scraping ``bluetoothctl`` output for them proved unreliable: any
failure (no controller, timeout, unknown device) read as "not paired".

This talks to bluetoothd over the system bus with dbus-fast, which bleak
already depends on under Linux, so no extra package is needed.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Optional

_LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
ADAPTER_INTERFACE = "org.bluez.Adapter1"
AGENT_MANAGER_INTERFACE = "org.bluez.AgentManager1"
AGENT_PATH = "/eu/edilkamin_ble/agent"
DEVICE_INTERFACE = "org.bluez.Device1"
OBJECT_MANAGER_INTERFACE = "org.freedesktop.DBus.ObjectManager"

# Reasons BlueZ (>= 5.7x) attaches to the Device1.Disconnected signal.
REASON_AUTHENTICATION = "org.bluez.Reason.Authentication"


class BlueZUnavailableError(Exception):
    """Raised when bluetoothd cannot be reached over D-Bus."""


@dataclass(frozen=True)
class BlueZDeviceState:
    """Snapshot of what BlueZ knows about the adapter and the stove."""

    adapter_path: Optional[str]
    device_path: Optional[str] = None
    paired: bool = False
    # Keys stored persistently. Older BlueZ has no Bonded property, in which
    # case this mirrors Paired.
    bonded: bool = False
    connected: bool = False

    @property
    def adapter_present(self) -> bool:
        return self.adapter_path is not None


def _value(props: dict[str, Any], name: str, default: Any = None) -> Any:
    variant = props.get(name)
    return default if variant is None else variant.value


class BlueZ:
    """Thin async wrapper around the parts of the BlueZ D-Bus API we need."""

    def __init__(self, mac_address: str):
        self._mac = mac_address.upper()
        self._bus = None
        self._disconnect_callback: Optional[Callable[[str, str], None]] = None

    @property
    def supported(self) -> bool:
        return sys.platform == "linux"

    async def _get_bus(self):
        if self._bus is not None and self._bus.connected:
            return self._bus

        from dbus_fast import BusType, Message
        from dbus_fast.aio import MessageBus

        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            raise BlueZUnavailableError(f"Cannot reach the system D-Bus: {e}") from e

        # Device1.Disconnected carries the reason for a dropped link. Without
        # it, "the stove rejected our keys" and "the adapter fell off USB"
        # look identical, and the old code deleted good bonds on the latter.
        rule = (
            f"type='signal',sender='{BLUEZ_SERVICE}',"
            f"interface='{DEVICE_INTERFACE}',member='Disconnected'"
        )
        await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            )
        )
        bus.add_message_handler(self._on_message)
        self._bus = bus
        return bus

    def set_disconnect_callback(
        self, callback: Optional[Callable[[str, str], None]]
    ) -> None:
        """Register ``callback(reason, message)`` for stove disconnects."""
        self._disconnect_callback = callback

    def _on_message(self, msg) -> None:
        if (
            msg.member != "Disconnected"
            or msg.interface != DEVICE_INTERFACE
            or not (msg.path or "").endswith(self._device_suffix)
        ):
            return None
        reason, message = (list(msg.body) + ["", ""])[:2]
        _LOGGER.debug("BlueZ reports disconnect: %s (%s)", reason, message)
        if self._disconnect_callback:
            self._disconnect_callback(reason, message)
        return None

    @property
    def _device_suffix(self) -> str:
        return "/dev_" + self._mac.replace(":", "_")

    async def _managed_objects(self) -> dict[str, dict[str, dict[str, Any]]]:
        from dbus_fast import Message, MessageType

        bus = await self._get_bus()
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path="/",
                interface=OBJECT_MANAGER_INTERFACE,
                member="GetManagedObjects",
            )
        )
        if reply.message_type == MessageType.ERROR:
            raise BlueZUnavailableError(
                f"bluetoothd did not answer: {reply.error_name} {reply.body}"
            )
        return reply.body[0]

    async def get_state(self) -> BlueZDeviceState:
        """Return the adapter and stove state as BlueZ currently sees it."""
        objects = await self._managed_objects()

        adapter_path = None
        for path, interfaces in sorted(objects.items()):
            adapter = interfaces.get(ADAPTER_INTERFACE)
            if adapter is not None and _value(adapter, "Powered", False):
                adapter_path = path
                break

        if adapter_path is None:
            return BlueZDeviceState(adapter_path=None)

        device_path = adapter_path + self._device_suffix
        device = objects.get(device_path, {}).get(DEVICE_INTERFACE)
        if device is None:
            return BlueZDeviceState(adapter_path=adapter_path)

        paired = bool(_value(device, "Paired", False))
        return BlueZDeviceState(
            adapter_path=adapter_path,
            device_path=device_path,
            paired=paired,
            bonded=bool(_value(device, "Bonded", paired)),
            connected=bool(_value(device, "Connected", False)),
        )

    async def disconnect_device(self, state: BlueZDeviceState) -> None:
        """Drop a BlueZ-level connection left over from an earlier session."""
        await self._device_call(state, DEVICE_INTERFACE, "Disconnect")

    async def remove_device(self, state: BlueZDeviceState) -> bool:
        """Forget the stove, including its bond. Returns True if removed."""
        if state.adapter_path is None or state.device_path is None:
            return False

        from dbus_fast import Message, MessageType

        bus = await self._get_bus()
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=state.adapter_path,
                interface=ADAPTER_INTERFACE,
                member="RemoveDevice",
                signature="o",
                body=[state.device_path],
            )
        )
        if reply.message_type == MessageType.ERROR:
            _LOGGER.warning("RemoveDevice failed: %s %s", reply.error_name, reply.body)
            return False
        return True

    async def _device_call(
        self, state: BlueZDeviceState, interface: str, member: str
    ) -> None:
        if state.device_path is None:
            return

        from dbus_fast import Message, MessageType

        bus = await self._get_bus()
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=state.device_path,
                interface=interface,
                member=member,
            )
        )
        if reply.message_type == MessageType.ERROR:
            _LOGGER.debug("%s.%s failed: %s", interface, member, reply.error_name)

    @contextlib.asynccontextmanager
    async def pairing_agent(self) -> AsyncIterator[None]:
        """Register an agent that approves Just Works pairing with the stove.

        Registered as the default agent only for the duration of the block,
        so it never answers for anything outside the add-on's own pairing
        attempts. See bluez_agent.py for why pairing fails without one.
        """
        from dbus_fast import Message, MessageType

        from .bluez_agent import StovePairingAgent

        bus = await self._get_bus()
        agent = StovePairingAgent(self._device_suffix)
        bus.export(AGENT_PATH, agent)

        async def manager_call(member: str, signature: str, body: list) -> bool:
            reply = await bus.call(
                Message(
                    destination=BLUEZ_SERVICE,
                    path="/org/bluez",
                    interface=AGENT_MANAGER_INTERFACE,
                    member=member,
                    signature=signature,
                    body=body,
                )
            )
            if reply.message_type == MessageType.ERROR:
                _LOGGER.warning("%s failed: %s %s", member, reply.error_name, reply.body)
                return False
            return True

        registered = await manager_call(
            "RegisterAgent", "os", [AGENT_PATH, "NoInputNoOutput"]
        )
        if registered:
            await manager_call("RequestDefaultAgent", "o", [AGENT_PATH])

        # The stove insists on bonding, and an adapter with Pairable off makes
        # the kernel ignore its Security Request. That is the default on some
        # Barrot-based USB dongles. Turn it on for the duration only:
        # re-encrypting with an existing bond does not need it.
        adapter_path = (await self.get_state()).adapter_path
        was_pairable = None
        if adapter_path is not None:
            was_pairable = await self._get_adapter_property(adapter_path, "Pairable")
            if was_pairable is False:
                await self._set_adapter_property(adapter_path, "Pairable", "b", True)
        try:
            yield
        finally:
            if was_pairable is False and bus.connected:
                await self._set_adapter_property(adapter_path, "Pairable", "b", False)
            if registered and bus.connected:
                await manager_call("UnregisterAgent", "o", [AGENT_PATH])
            bus.unexport(AGENT_PATH, agent)

    async def _get_adapter_property(self, adapter_path: str, name: str) -> Any:
        from dbus_fast import Message, MessageType

        bus = await self._get_bus()
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=adapter_path,
                interface="org.freedesktop.DBus.Properties",
                member="Get",
                signature="ss",
                body=[ADAPTER_INTERFACE, name],
            )
        )
        if reply.message_type == MessageType.ERROR:
            _LOGGER.debug("Get %s failed: %s", name, reply.error_name)
            return None
        return reply.body[0].value

    async def _set_adapter_property(
        self, adapter_path: str, name: str, signature: str, value: Any
    ) -> None:
        from dbus_fast import Message, MessageType, Variant

        bus = await self._get_bus()
        reply = await bus.call(
            Message(
                destination=BLUEZ_SERVICE,
                path=adapter_path,
                interface="org.freedesktop.DBus.Properties",
                member="Set",
                signature="ssv",
                body=[ADAPTER_INTERFACE, name, Variant(signature, value)],
            )
        )
        if reply.message_type == MessageType.ERROR:
            _LOGGER.warning("Setting %s failed: %s %s", name, reply.error_name, reply.body)
        else:
            _LOGGER.info("Adapter %s set to %s", name, value)

    def close(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
