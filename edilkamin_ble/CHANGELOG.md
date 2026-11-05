# Changelog

## 1.3.1

First public release.

### Features

- Local control of Edilkamin pellet stoves with "The Mind" module over
  Bluetooth Low Energy: no cloud account, no app.
- Home Assistant entities through MQTT discovery: a climate entity (on/off,
  target temperature, P1–P5 presets, fan speed), room and exhaust temperature
  with long-term statistics, operating phase, alarm (with code and
  description), pellet supply, and settings switches.
- Stove phase decoded as the official Edilkamin app does it: operational phase
  plus ignition sub-phase.
- All 25 Edilkamin alarm codes, with descriptions.

### Pairing and connection

- Pairing is handled entirely by the add-on: a BlueZ pairing agent approves
  "Just Works" pairing for the configured stove only.
- **Pair Stove** button and **Bluetooth Status** sensor in Home Assistant.
- Automatic re-pairing when the stove discards its keys, which it does on its
  own from time to time.
- Per-device 20 s LE supervision timeout through the kernel management API.
  The Linux default of 420 ms cannot hold a link at a typical stove distance.
- Exponential reconnect backoff. Short reconnects keep the last values, and
  entities are only marked unavailable after 3 minutes without contact.

### Robustness

- Commands and polling are serialised over the single BLE link, and responses
  are checked against the request that produced them.
- Retained MQTT commands are ignored and cleared, so they cannot switch the
  stove off on every start.
- MQTT Last Will on a separate add-on availability topic, so the pairing
  controls stay usable while the stove is unreachable.
