# Edilkamin BLE for Home Assistant

A Home Assistant add-on that controls Edilkamin pellet stoves with the
**"The Mind"** module directly over Bluetooth Low Energy. Everything stays
local: no Edilkamin cloud account and no phone app.

> **Unofficial project.** This add-on is not affiliated with, endorsed by or
> supported by Edilkamin S.p.A. "Edilkamin" and "The Mind" are trademarks of
> their respective owner.

## ⚠️ Safety

A pellet stove is a combustion appliance. This add-on talks to it through a
reverse-engineered protocol and is provided **as is, without any warranty**
(see [LICENSE](LICENSE)).

- Do not rely on it, or on Home Assistant automations built on it, as a safety
  device. Keep the stove's own safety features, smoke and CO detectors, and
  regular maintenance in place.
- Starting the stove remotely means nobody may be watching it. Follow the
  stove manual's guidance on unattended operation.
- Use at your own risk.

## Features

- **Climate entity:** on/off, target temperature (10–30 °C), power level presets
  P1–P5 and Auto, and fan speed.
- **Sensors:** room and exhaust temperature (with long-term statistics), stove
  phase (Cleaning, Loading pellets, Warming up, Running, Cooling down, …), and
  pellet supply.
- **Alarms:** all 25 Edilkamin alarm codes with descriptions (e.g. *Pellet tank
  empty*, *Ignition failed*, *Power outage*).
- **Settings:** Relax, Chrono, Standby and Automatic mode switches.
- **Pairing built in:** the add-on pairs with the stove by itself and re-pairs
  automatically when the stove forgets the pairing. There is also a **Pair
  Stove** button and a **Bluetooth Status** sensor.
- **Robust connection:** automatic reconnects, a longer Bluetooth supervision
  timeout for links at a typical stove distance, and no "unavailable" flicker
  on short reconnects.
- Entities appear automatically via MQTT discovery.

## Installation

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FStopThePcap%2Fedilkamin-ble)

1. Add this repository in Home Assistant: **Settings → Add-ons → Add-on Store
   → ⋮ → Repositories**, and enter `https://github.com/StopThePcap/edilkamin-ble`.
2. Install **Edilkamin BLE**.
3. Set your stove's Bluetooth MAC address in the add-on configuration, along
   with your MQTT broker details if you don't use the Mosquitto add-on.
4. Start the add-on. The stove appears as a device under the MQTT integration
   and pairs automatically.

## Requirements

- Home Assistant OS or Supervised (the add-on needs the Supervisor), on a
  64-bit system (amd64 or aarch64, e.g. a Raspberry Pi 4/5 with 64-bit OS)
- An Edilkamin stove with "The Mind" module
- A Bluetooth adapter that supports BLE on Linux (see
  [Tested Hardware](edilkamin_ble/DOCS.md#tested-hardware))
- An MQTT broker, e.g. the Mosquitto add-on

## Status

Reading all stove status, switching the stove on and off, and setting the target
temperature are verified on a real stove. Power level, fan speed, and the
Relax/Chrono/Standby/Automatic switches have not been verified yet: feedback is
welcome.

## Documentation

- [DOCS.md](edilkamin_ble/DOCS.md): configuration, entities, pairing,
  troubleshooting, tested hardware
- [TECHNICAL.md](edilkamin_ble/TECHNICAL.md): protocol, architecture,
  pairing and connection internals

## Credits

The Bluetooth protocol was reverse-engineered by others. This add-on is an
independent Python implementation building on their published findings:

- [netmb/Edilkamin_BT](https://github.com/netmb/Edilkamin_BT): ESP32 firmware;
  packet format, encryption and registers
- [AndreMiras/edilkamin.js](https://github.com/AndreMiras/edilkamin.js) (MIT):
  phase and alarm code tables from the official app

## License

[Apache License 2.0](LICENSE)
