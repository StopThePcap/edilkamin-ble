"""MQTT Discovery for Home Assistant entity registration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from . import __version__
from .const import BT_STATUSES, MQTT_BASE_TOPIC, MQTT_DISCOVERY_PREFIX, PHASE_LABELS

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceInfo:
    """Device information for MQTT discovery."""

    identifier: str
    name: str
    manufacturer: str = "Edilkamin"
    model: str = "Pellet Stove"
    sw_version: str = __version__


class MQTTDiscovery:
    """Generates and publishes MQTT discovery configurations."""

    def __init__(
        self,
        device_info: DeviceInfo,
        base_topic: str = MQTT_BASE_TOPIC,
        discovery_prefix: str = MQTT_DISCOVERY_PREFIX,
    ):
        """Initialize MQTT Discovery.

        Args:
            device_info: Device information for discovery.
            base_topic: Base topic for state/command messages.
            discovery_prefix: Home Assistant discovery prefix.
        """
        self._device = device_info
        self._base_topic = base_topic
        self._discovery_prefix = discovery_prefix
        self._device_topic = f"{base_topic}/{device_info.identifier}"

    def _device_payload(self) -> dict[str, Any]:
        """Generate device info payload."""
        return {
            "identifiers": [self._device.identifier],
            "name": self._device.name,
            "manufacturer": self._device.manufacturer,
            "model": self._device.model,
            "sw_version": self._device.sw_version,
        }

    def _availability_payload(self) -> dict[str, Any]:
        """Availability for stove entities: add-on running *and* stove reachable.

        The add-on's Last Will lands on the bridge topic, so a crash greys
        everything out even though the stove topic still says "online".
        """
        return {
            "availability": [
                self._availability_topic("bridge"),
                self._availability_topic("availability"),
            ],
            "availability_mode": "all",
        }

    def _bridge_availability_payload(self) -> dict[str, Any]:
        """Availability for add-on entities that must work without the stove.

        The pairing button in particular is needed exactly when the stove is
        unreachable, so it cannot share the stove's availability.
        """
        return {"availability": [self._availability_topic("bridge")]}

    def _availability_topic(self, name: str) -> dict[str, Any]:
        return {
            "topic": f"{self._device_topic}/{name}",
            "payload_available": "online",
            "payload_not_available": "offline",
        }

    def get_all_discovery_configs(self) -> list[tuple[str, dict[str, Any]]]:
        """Get all discovery configurations.

        Returns:
            List of (topic, payload) tuples for publishing.
        """
        configs = []

        # Climate entity (main control)
        configs.append(self._climate_config())

        # Everything the climate entity already covers (on/off, preset, fan,
        # automatic mode) is kept for automations but filed under
        # diagnostic/config, so the device page and auto-generated dashboards
        # show one thermostat plus what it cannot show.

        # Temperatures. state_class gives long-term statistics and real
        # history graphs.
        configs.append(self._sensor_config(
            "current_temperature", "Room Temperature",
            device_class="temperature", unit="°C",
            state_class="measurement", suggested_display_precision=1,
        ))
        configs.append(self._sensor_config(
            "exhaust_temperature", "Exhaust Temperature",
            device_class="temperature", unit="°C", icon="mdi:fire",
            state_class="measurement", suggested_display_precision=0,
        ))

        # Status
        configs.append(self._sensor_config(
            "phase", "Phase",
            icon="mdi:state-machine",
            device_class="enum", options=list(PHASE_LABELS.values()),
            # Raw register values, so an unexpected state stays diagnosable.
            json_attributes_topic=f"{self._device_topic}/phase/attributes",
        ))
        configs.append(self._binary_sensor_config(
            "alarm", "Alarm",
            device_class="problem",
            # Alarm code and its description from the Edilkamin app.
            json_attributes_topic=f"{self._device_topic}/alarm/attributes",
        ))
        configs.append(self._binary_sensor_config(
            "pellet_empty", "Pellet Supply",
            device_class="problem", icon="mdi:grain",
        ))

        # Quick on/off for dashboards, voice assistants and automations.
        configs.append(self._switch_config(
            "power", "Power",
            icon="mdi:power",
        ))

        # Settings
        configs.append(self._switch_config(
            "relax", "Relax Mode",
            icon="mdi:volume-off", entity_category="config",
        ))
        configs.append(self._switch_config(
            "chrono", "Chrono Mode",
            icon="mdi:clock-outline", entity_category="config",
        ))
        configs.append(self._switch_config(
            "standby", "Standby Mode",
            icon="mdi:sleep", entity_category="config",
        ))
        # Same as the climate's "Auto" preset.
        configs.append(self._switch_config(
            "automatic", "Automatic Mode",
            icon="mdi:refresh-auto", entity_category="config",
        ))

        # Duplicates of climate state, kept as diagnostics.
        configs.append(self._binary_sensor_config(
            "running", "Running",
            device_class="running", entity_category="diagnostic",
        ))
        configs.append(self._sensor_config(
            "power_level", "Power Level",
            icon="mdi:fire", entity_category="diagnostic",
        ))
        configs.append(self._sensor_config(
            "fan_speed", "Fan Speed",
            icon="mdi:fan", entity_category="diagnostic",
        ))

        # Add-on entities (stay available while the stove is unreachable)
        configs.append(self._bluetooth_status_config())
        configs.append(self._pair_button_config())

        return configs

    def _bluetooth_status_config(self) -> tuple[str, dict[str, Any]]:
        """Generate the Bluetooth link / pairing status sensor config."""
        unique_id = f"{self._device.identifier}_bluetooth_status"
        topic = f"{self._discovery_prefix}/sensor/{unique_id}/config"
        payload = {
            "name": "Bluetooth Status",
            "unique_id": unique_id,
            "device": self._device_payload(),
            **self._bridge_availability_payload(),
            "state_topic": f"{self._device_topic}/bluetooth_status",
            "icon": "mdi:bluetooth",
            "device_class": "enum",
            "options": list(BT_STATUSES),
            "entity_category": "diagnostic",
        }
        return (topic, payload)

    def _pair_button_config(self) -> tuple[str, dict[str, Any]]:
        """Generate the "Pair Stove" button config."""
        unique_id = f"{self._device.identifier}_pair"
        topic = f"{self._discovery_prefix}/button/{unique_id}/config"
        payload = {
            "name": "Pair Stove",
            "unique_id": unique_id,
            "device": self._device_payload(),
            **self._bridge_availability_payload(),
            "command_topic": f"{self._device_topic}/pair/set",
            "payload_press": "PRESS",
            "icon": "mdi:bluetooth-connect",
            "entity_category": "config",
        }
        return (topic, payload)

    def _climate_config(self) -> tuple[str, dict[str, Any]]:
        """Generate climate entity discovery config."""
        unique_id = f"{self._device.identifier}_climate"
        topic = f"{self._discovery_prefix}/climate/{self._device.identifier}/config"

        payload = {
            # None = use the device name. Repeating it here made Home
            # Assistant show "Edilkamin Stove Edilkamin Stove".
            "name": None,
            "unique_id": unique_id,
            "icon": "mdi:fireplace",
            "device": self._device_payload(),
            **self._availability_payload(),

            # Modes
            "modes": ["off", "heat"],
            "mode_state_topic": f"{self._device_topic}/mode",
            "mode_command_topic": f"{self._device_topic}/mode/set",

            # Presets (power levels)
            "preset_modes": ["Auto", "P1", "P2", "P3", "P4", "P5"],
            "preset_mode_state_topic": f"{self._device_topic}/preset",
            "preset_mode_command_topic": f"{self._device_topic}/preset/set",

            # Fan modes
            "fan_modes": ["Auto", "20%", "40%", "60%", "80%", "100%"],
            "fan_mode_state_topic": f"{self._device_topic}/fan_mode",
            "fan_mode_command_topic": f"{self._device_topic}/fan_mode/set",

            # Temperature
            "temperature_unit": "C",
            "min_temp": 10,
            "max_temp": 30,
            "temp_step": 0.5,
            "precision": 0.1,
            "current_temperature_topic": f"{self._device_topic}/current_temperature",
            "temperature_state_topic": f"{self._device_topic}/target_temperature",
            "temperature_command_topic": f"{self._device_topic}/target_temperature/set",

            # Action
            "action_topic": f"{self._device_topic}/action",

            # NOTE: `retain` is deliberately left off (i.e. false). With it set,
            # Home Assistant retains every command it publishes, and the broker
            # replays all of them the moment we re-subscribe after a reconnect —
            # a burst of writes and polls that the stove's single BLE link
            # cannot absorb.
            "optimistic": False,
        }

        return (topic, payload)

    def _sensor_config(
        self,
        sensor_id: str,
        name: str,
        device_class: str | None = None,
        unit: str | None = None,
        icon: str | None = None,
        **extra: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Generate sensor discovery config.

        ``extra`` is merged into the payload as-is (entity_category,
        state_class, options, ...).
        """
        unique_id = f"{self._device.identifier}_{sensor_id}"
        topic = f"{self._discovery_prefix}/sensor/{self._device.identifier}_{sensor_id}/config"

        payload = {
            "name": name,
            "unique_id": unique_id,
            "device": self._device_payload(),
            **self._availability_payload(),
            "state_topic": f"{self._device_topic}/{sensor_id}",
        }

        if device_class:
            payload["device_class"] = device_class
        if unit:
            payload["unit_of_measurement"] = unit
        if icon:
            payload["icon"] = icon
        payload.update(extra)

        return (topic, payload)

    def _binary_sensor_config(
        self,
        sensor_id: str,
        name: str,
        device_class: str | None = None,
        icon: str | None = None,
        **extra: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Generate binary sensor discovery config."""
        unique_id = f"{self._device.identifier}_{sensor_id}"
        topic = f"{self._discovery_prefix}/binary_sensor/{self._device.identifier}_{sensor_id}/config"

        payload = {
            "name": name,
            "unique_id": unique_id,
            "device": self._device_payload(),
            **self._availability_payload(),
            "state_topic": f"{self._device_topic}/{sensor_id}",
            "payload_on": "ON",
            "payload_off": "OFF",
        }

        if device_class:
            payload["device_class"] = device_class
        if icon:
            payload["icon"] = icon
        payload.update(extra)

        return (topic, payload)

    def _switch_config(
        self,
        switch_id: str,
        name: str,
        icon: str | None = None,
        **extra: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Generate switch discovery config."""
        unique_id = f"{self._device.identifier}_{switch_id}"
        topic = f"{self._discovery_prefix}/switch/{self._device.identifier}_{switch_id}/config"

        payload = {
            "name": name,
            "unique_id": unique_id,
            "device": self._device_payload(),
            **self._availability_payload(),
            "state_topic": f"{self._device_topic}/{switch_id}",
            "command_topic": f"{self._device_topic}/{switch_id}/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            # See _climate_config: retaining commands causes a replay storm on
            # every MQTT reconnect.
            "optimistic": False,
        }

        if icon:
            payload["icon"] = icon
        payload.update(extra)

        return (topic, payload)

    def get_state_topics(self) -> dict[str, str]:
        """Get mapping of state names to MQTT topics.

        Returns:
            Dict mapping state name to full topic path.
        """
        return {
            "availability": f"{self._device_topic}/availability",
            "bridge": f"{self._device_topic}/bridge",
            "bluetooth_status": f"{self._device_topic}/bluetooth_status",
            "mode": f"{self._device_topic}/mode",
            "preset": f"{self._device_topic}/preset",
            "fan_mode": f"{self._device_topic}/fan_mode",
            "current_temperature": f"{self._device_topic}/current_temperature",
            "target_temperature": f"{self._device_topic}/target_temperature",
            "action": f"{self._device_topic}/action",
            "exhaust_temperature": f"{self._device_topic}/exhaust_temperature",
            "phase": f"{self._device_topic}/phase",
            "phase_attributes": f"{self._device_topic}/phase/attributes",
            "alarm_attributes": f"{self._device_topic}/alarm/attributes",
            "power_level": f"{self._device_topic}/power_level",
            "fan_speed": f"{self._device_topic}/fan_speed",
            "running": f"{self._device_topic}/running",
            "alarm": f"{self._device_topic}/alarm",
            "pellet_empty": f"{self._device_topic}/pellet_empty",
            "power": f"{self._device_topic}/power",
            "relax": f"{self._device_topic}/relax",
            "chrono": f"{self._device_topic}/chrono",
            "standby": f"{self._device_topic}/standby",
            "automatic": f"{self._device_topic}/automatic",
        }

    def get_command_topics(self) -> dict[str, str]:
        """Get mapping of command names to MQTT topics.

        Returns:
            Dict mapping command name to full topic path.
        """
        return {
            "mode": f"{self._device_topic}/mode/set",
            "preset": f"{self._device_topic}/preset/set",
            "fan_mode": f"{self._device_topic}/fan_mode/set",
            "target_temperature": f"{self._device_topic}/target_temperature/set",
            "power": f"{self._device_topic}/power/set",
            "relax": f"{self._device_topic}/relax/set",
            "chrono": f"{self._device_topic}/chrono/set",
            "standby": f"{self._device_topic}/standby/set",
            "automatic": f"{self._device_topic}/automatic/set",
            "pair": f"{self._device_topic}/pair/set",
        }
