"""Configuration management for Edilkamin BLE add-on."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# Default Home Assistant add-on config path
ADDON_CONFIG_PATH = "/data/options.json"


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
                return cls.from_dict(data)
            except Exception as e:
                _LOGGER.warning("Failed to load config file: %s", e)

        # Fall back to environment variables
        _LOGGER.info("Loading config from environment variables")
        return cls.from_env()

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
