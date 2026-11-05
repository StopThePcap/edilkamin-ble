"""Configuration management for Edilkamin BLE add-on."""

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Default Home Assistant add-on config path
ADDON_CONFIG_PATH = "/data/options.json"

# Broker details the Supervisor hands to add-ons that declare `mqtt:need`.
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
DEFAULT_MQTT_HOST = "core-mosquitto"


def _supervisor_mqtt_service() -> tuple[Optional[dict], str]:
    """Ask the Supervisor for the MQTT broker's host, port and credentials.

    Returns:
        (service data or None, reason when None).
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None, "not running as a Home Assistant add-on"
    request = urllib.request.Request(
        SUPERVISOR_MQTT_URL, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.load(response)
    except Exception as e:  # HTTP 400 when no MQTT broker add-on provides it
        return None, f"no MQTT service available from the Supervisor ({e})"
    data = body.get("data") or {}
    if body.get("result") != "ok" or not data.get("host"):
        return None, "no MQTT service available from the Supervisor"
    return data, ""


@dataclass
class Config:
    """Add-on configuration."""

    # BLE settings
    ble_mac_address: str

    # MQTT settings
    mqtt_host: str = "core-mosquitto"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_discovery_prefix: str = "homeassistant"

    # Device settings
    device_name: str = "Edilkamin Stove"
    device_id: Optional[str] = None  # Auto-generated from MAC if not set

    # Polling settings
    poll_interval: int = 30  # seconds

    # Logging
    log_level: str = "info"

    # Where the MQTT settings came from, for the startup log (load() runs
    # before logging is configured).
    mqtt_source: str = "add-on configuration"

    def __post_init__(self) -> None:
        """Post-initialization processing."""
        # Normalize MAC address
        self.ble_mac_address = self.ble_mac_address.upper().replace("-", ":")

        # Generate device ID from MAC if not set
        if not self.device_id:
            self.device_id = self.ble_mac_address.replace(":", "")

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Create Config from dictionary.

        Args:
            data: Configuration dictionary.

        Returns:
            Config instance.
        """
        return cls(
            ble_mac_address=data.get("ble_mac_address", ""),
            mqtt_host=data.get("mqtt_host", "core-mosquitto"),
            mqtt_port=data.get("mqtt_port", 1883),
            mqtt_username=data.get("mqtt_username") or None,
            mqtt_password=data.get("mqtt_password") or None,
            mqtt_discovery_prefix=data.get("mqtt_discovery_prefix", "homeassistant"),
            device_name=data.get("device_name", "Edilkamin Stove"),
            device_id=data.get("device_id"),
            poll_interval=data.get("poll_interval", 30),
            log_level=data.get("log_level", "info"),
        )

    @classmethod
    def from_env(cls) -> "Config":
        """Create Config from environment variables.

        Returns:
            Config instance.
        """
        return cls(
            ble_mac_address=os.environ.get("BLE_MAC_ADDRESS", ""),
            mqtt_host=os.environ.get("MQTT_HOST", "core-mosquitto"),
            mqtt_port=int(os.environ.get("MQTT_PORT", "1883")),
            mqtt_username=os.environ.get("MQTT_USERNAME") or None,
            mqtt_password=os.environ.get("MQTT_PASSWORD") or None,
            mqtt_discovery_prefix=os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant"),
            device_name=os.environ.get("DEVICE_NAME", "Edilkamin Stove"),
            device_id=os.environ.get("DEVICE_ID"),
            poll_interval=int(os.environ.get("POLL_INTERVAL", "30")),
            log_level=os.environ.get("LOG_LEVEL", "info"),
        )

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        """Load configuration from file or environment.

        Tries to load from Home Assistant add-on config first,
        then falls back to environment variables.

        Args:
            config_path: Optional path to config file.

        Returns:
            Config instance.
        """
        path = config_path or ADDON_CONFIG_PATH

        # Try to load from file
        if Path(path).exists():
            _LOGGER.info("Loading config from %s", path)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                config = cls.from_dict(data)
                config._use_supervisor_mqtt_if_unset()
                return config
            except Exception as e:
                _LOGGER.warning("Failed to load config file: %s", e)

        # Fall back to environment variables
        _LOGGER.info("Loading config from environment variables")
        return cls.from_env()

    def _use_supervisor_mqtt_if_unset(self) -> None:
        """Take broker details from the Supervisor unless set explicitly.

        With the Mosquitto add-on, anonymous access is off, so a blank
        username would otherwise just fail to connect. The Supervisor provides
        a broker account to add-ons that declare `mqtt:need`, which this
        add-on does. An explicitly configured username, or a broker other than
        the default host, is always used as configured.
        """
        if self.mqtt_username or self.mqtt_host not in ("", DEFAULT_MQTT_HOST):
            return
        service, reason = _supervisor_mqtt_service()
        if service is None:
            self.mqtt_source = f"add-on configuration ({reason})"
            return
        self.mqtt_host = service["host"]
        self.mqtt_port = int(service.get("port") or self.mqtt_port)
        self.mqtt_username = service.get("username") or None
        self.mqtt_password = service.get("password") or None
        self.mqtt_source = "MQTT broker add-on (credentials from the Supervisor)"

    def validate(self) -> list[str]:
        """Validate configuration.

        Returns:
            List of validation error messages.
        """
        errors = []

        if not self.ble_mac_address:
            errors.append("BLE MAC address is required")
        elif len(self.ble_mac_address.replace(":", "")) != 12:
            errors.append(f"Invalid BLE MAC address: {self.ble_mac_address}")

        if not self.mqtt_host:
            errors.append("MQTT host is required")

        if self.poll_interval < 5:
            errors.append("Poll interval must be at least 5 seconds")

        return errors

    def setup_logging(self) -> None:
        """Configure logging based on log level."""
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(name)s %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        _LOGGER.info("Logging configured at %s level", self.log_level.upper())
