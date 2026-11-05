# Edilkamin BLE Add-on

Local Bluetooth control for Edilkamin pellet stoves equipped with "The Mind" WiFi/Bluetooth module.

> **Unofficial and unverified by Edilkamin.** A pellet stove is a combustion
> appliance. Do not rely on this add-on or automations built on it as a safety
> device, keep the stove's own safety features and detectors in place, and
> follow the stove manual on unattended operation. Use at your own risk.

## Features

- **Local Control**: Direct Bluetooth communication with your stove - no cloud dependency
- **Full Climate Control**: Temperature, power levels, and fan speed control
- **MQTT Integration**: Uses MQTT Discovery for seamless Home Assistant integration
- **Multiple Entities**: Climate, sensors, binary sensors, and switches

## Prerequisites

1. **Edilkamin stove with "The Mind" module** - The stove must have the Edilkamin WiFi/Bluetooth module installed
2. **Bluetooth adapter** - A Bluetooth Low Energy (BLE) compatible adapter connected to your Home Assistant host
3. **MQTT** - The **Mosquitto broker** add-on, and the **MQTT** integration
   set up in Home Assistant (*Settings → Devices & services*; Home Assistant
   offers it automatically once Mosquitto runs). Another MQTT broker works too,
   see Configuration.

## Installation

1. Go to **Settings** > **Add-ons** > **Add-on Store**
2. Click the three dots menu and select **Repositories**
3. Add `https://github.com/StopThePcap/edilkamin-ble`
4. Find "Edilkamin BLE" in the store and click **Install**

The add-on is built locally on install, which takes a few minutes on a
Raspberry Pi.

Alternatively, copy the `edilkamin_ble` folder of this repository into your
Home Assistant `/addons` directory and install it from **Local add-ons**.

## Tested Hardware

| | Status |
|---|---|
| Edilkamin stove with "The Mind" module (advertises as `EDILKAMIN_EP`) | Works |
| Intel AX200 Bluetooth (USB `8087:0029`) | Works |
| Barrot-based USB dongles, e.g. some "BT 5.4" sticks (USB `33fa:0010`) | **Does not work**: cannot complete new pairings on Linux |

Other adapters that Linux supports well for BLE (Intel, Realtek) should work.
Running Home Assistant in a VM is fine with USB passthrough. Make sure the
hypervisor host itself does not load `btusb`, or it can grab the adapter back
when the adapter re-enumerates (Intel adapters do so on every firmware load).

### Verified Functions

Reading all status, plus switching the stove on and off and setting the target
temperature, have been verified on a real stove. Setting the power level, the
fan speed, and the Relax, Chrono, Standby and Automatic switches use register
values from the reverse-engineered protocol but have **not been verified** yet.
Reports are welcome.

### Permissions

The add-on asks for more access than most add-ons. Each item has one purpose:

| Setting | Why |
|---|---|
| `host_network` | Bluetooth access through the host's BlueZ |
| `host_dbus` | Talking to the host's `bluetoothd` (state, pairing, pairing agent) |
| `privileged: NET_ADMIN` | Setting the stove's LE connection parameters (supervision timeout) through the kernel's Bluetooth management API |

## Configuration

### Finding Your Stove's MAC Address

1. Enable Bluetooth on your phone
2. Open the Edilkamin app
3. Look for your stove - the MAC address is usually visible in the connection settings
4. Alternatively, use `bluetoothctl scan on` on your Home Assistant host to find devices starting with `EDILKAMIN_`

### Configuration Options

| Option | Description | Default |
|--------|-------------|---------|
| `ble_mac_address` | Bluetooth MAC address of your stove (required) | - |
| `poll_interval` | Seconds between status polls | 30 |
| `mqtt_host` | MQTT broker hostname | core-mosquitto |
| `mqtt_port` | MQTT broker port | 1883 |
| `mqtt_username` | MQTT username. **Leave empty with the Mosquitto add-on** | - |
| `mqtt_password` | MQTT password. **Leave empty with the Mosquitto add-on** | - |
| `mqtt_discovery_prefix` | Home Assistant MQTT discovery prefix | homeassistant |
| `device_name` | Name shown in Home Assistant | Edilkamin Stove |
| `log_level` | Logging level (debug/info/warning/error) | info |

### MQTT

**With the Mosquitto broker add-on (most setups), leave all MQTT options at
their defaults and the username and password empty.** The add-on then gets the
broker address and an account from the Home Assistant Supervisor. The startup
log confirms it:

```
MQTT: core-mosquitto:1883, user addons (from MQTT broker add-on (credentials from the Supervisor))
```

With **another broker**, set `mqtt_host`, `mqtt_port` and, if the broker needs
them, `mqtt_username` and `mqtt_password`. Settings you enter are always used
as entered.

### Example Configuration

With the Mosquitto add-on, only the stove address is needed:

```yaml
ble_mac_address: "AA:BB:CC:DD:EE:FF"
```

Optional settings, shown with their defaults:

```yaml
poll_interval: 30
device_name: "Edilkamin Stove"
log_level: "info"
```

## Exposed Entities

All entities belong to one device. Everyday entities are listed first. Settings
are under *Configuration* and duplicates of the thermostat under *Diagnostic*,
so auto-generated dashboards stay short.

### Main
- **Edilkamin Stove** (climate): on/off, target temperature (10–30 °C in 0.5°
  steps), presets Auto / P1–P5 (power level), fan Auto / 20–100 %. Its action
  shows *preheating* during ignition and *heating* once running.
- **Room Temperature**, **Exhaust Temperature**: with long-term statistics
- **Phase**: Off, Cleaning, Loading pellets, Loading pause, Checking exhaust,
  Reaching temperature, Warming up, Running, Shutting down, Cooling down, Final
  cleaning. The raw register codes are in its attributes.
- **Alarm**, **Pellet Supply**: problem sensors (OK / Problem). The Alarm's
  attributes give the alarm code and what it means (e.g. "Pellet tank empty",
  "Door open").
- **Power**: on/off switch, the same as the thermostat's mode, for dashboards
  and voice assistants

### Configuration
- **Relax Mode**, **Chrono Mode**, **Standby Mode**, **Automatic Mode** (the
  same as the *Auto* preset)
- **Pair Stove**: see below

### Diagnostic
- **Bluetooth Status**: see below
- **Running**, **Power Level**, **Fan Speed**: the same information as the
  thermostat, kept for automations

## Pairing the Stove

The stove must be paired with your Home Assistant host's Bluetooth adapter
before it will answer commands. The pairing persists across reboots.

When no pairing is stored, the add-on pairs by itself on its first connection.
You normally do not have to do anything. Use the steps below to re-pair, for
example after changing the Bluetooth adapter or if **Bluetooth Status** shows
`Pairing required`.

### From Home Assistant (recommended)

1. Start the add-on.
2. Open the Edilkamin Stove device and press **Pair Stove**. The
   **Bluetooth Status** sensor changes to `Pairing`.
3. The add-on removes any old pairing, watches for the stove for 3 minutes,
   and pairs as soon as it accepts. If it has not paired within a minute,
   power-cycle the stove (switch it off at the mains and back on).
4. **Bluetooth Status** changes to `Connected` on success, or `Pairing failed`
   if the window passed. In that case, just try again.

Close the Edilkamin phone app first: the stove accepts one connection at a time.

### Manually (fallback)

Stop the add-on, copy `pair_stove.sh` to the HA host and run it. It removes
the old pairing first; without that, `bluetoothctl pair` answers "AlreadyExists"
and nothing changes. Pairing needs an agent that confirms the request: the
script feeds `yes` to bluetoothctl's agent for that. Pairing with a one-shot
`bluetoothctl pair` fails with "Authentication Failed".

### Bluetooth Status values

| Status | Meaning | What to do |
|--------|---------|------------|
| `Connected` | Talking to the stove | — |
| `Connecting` / `Disconnected` | Link down, retrying with backoff | Usually nothing; see Connection Drops |
| `No adapter` | BlueZ has no powered Bluetooth adapter | Check the adapter is attached to the host (`bluetoothctl list`) |
| `Not paired` | No pairing stored for the stove | Press **Pair Stove** |
| `Pairing required` | A pairing is stored, but the stove keeps rejecting it | Press **Pair Stove** |
| `Pairing` | Pairing mode is active | Wait; power-cycle the stove if it takes over a minute |
| `Pairing failed` | Pairing mode ended without success | Press **Pair Stove** and try again |

The stove does not keep pairings reliably: it has been seen discarding one
seconds to minutes after it was made, so the cause is in its firmware. The
add-on handles this by itself. When the stove rejects our key (or three
connections in a row fail during setup), it re-pairs at once, which takes a few
seconds and needs no power-cycle. Only if that automatic re-pair *fails* does it
wait 5 minutes before trying again. A missing or resetting Bluetooth adapter
never triggers this. The stove does require pairing: unpaired, it disconnects
right after service discovery.

### Finding Your Stove's MAC Address

Run `bluetoothctl scan on` on the HA host and look for a device named `EDILKAMIN_*`. Alternatively, check the Edilkamin app's Bluetooth connection settings.

## Troubleshooting

### Stove Disconnects Immediately

Check **Bluetooth Status** first. `Not paired` or `Pairing required` means the
stove is not (or no longer) paired: see Pairing the Stove. If the add-on log also
shows `No adapter` around the same time, the adapter itself is resetting: check
`dmesg` for USB disconnects. Some cheap Bluetooth 5.x dongles are not well
supported on Linux and fall off the USB bus when a connection is opened.

### "Notify acquired" Error

A stale BLE connection from a previous attempt. The add-on handles this automatically on retry, but you can also restart the add-on.

### Stove Not Found

1. Ensure the stove is powered on
2. Close the Edilkamin app on your phone (the stove only allows one BLE connection)
3. Verify the MAC address is correct
4. Ensure your Bluetooth adapter supports BLE
5. Check the add-on logs for connection errors

### Connection Drops

The add-on sets a 20 s supervision timeout for the stove (see TECHNICAL.md).
Linux defaults to 420 ms, which a weak signal cannot sustain. If the log says
it could not set Bluetooth connection parameters, the add-on is missing its
`NET_ADMIN` permission; reinstall or rebuild it so the Supervisor applies
`config.yaml`.

1. Ensure your Bluetooth adapter is within range of the stove
2. Consider using a USB extension to position the adapter closer
3. Check for Bluetooth interference from other devices
4. Increase the `poll_interval` if the connection is unstable
5. If Home Assistant's own Bluetooth integration uses the same adapter, its
   scanning competes with the add-on. Give the add-on a dedicated adapter, or
   exclude the stove's MAC from the Bluetooth integration.

The add-on reconnects on its own with an exponential backoff (5s up to 5
minutes), so occasional drops in the log are expected and self-healing.
Entities keep their last values through short reconnects and only go
unavailable after the stove has been unreachable for 3 minutes.

### Entities Show "Unavailable" But The Log Looks Fine

A stale retained `offline` message may still be on the broker. Restart the
add-on, or clear `edilkamin/<device_id>/availability` with MQTT Explorer.

### MQTT Issues

**"The MQTT broker rejected the login"** in the log: with the Mosquitto add-on,
clear `mqtt_username` and `mqtt_password` so the add-on uses the
Supervisor-provided account. With another broker, check the username and
password.

**The add-on runs but no stove device appears:** set up the MQTT integration
in Home Assistant (*Settings → Devices & services*). The add-on publishes its
entities through MQTT discovery, which the integration picks up.

Otherwise:

1. Verify the Mosquitto add-on is running
2. Check the first lines of the add-on log: they show which broker and user it
   connects with
3. Use MQTT Explorer to check that discovery messages appear under
   `homeassistant/`

### Debug Logging

Set `log_level: "debug"` in the configuration to see detailed communication logs.

## Technical Details

This add-on communicates with the stove using:
- **BLE Service**: `0000abf0-0000-1000-8000-00805f9b34fb`
- **Protocol**: AES-128-CBC encrypted Modbus RTU over BLE
- **Packet Size**: 32 bytes per message
- **Characteristics**: ABF1 (write commands), ABF2 (receive responses via notify)

See [TECHNICAL.md](TECHNICAL.md) for the full protocol specification.

Based on protocol research from:
- [netmb/Edilkamin_BT](https://github.com/netmb/Edilkamin_BT) — ESP32 implementation
- [AndreMiras/edilkamin.js](https://github.com/AndreMiras/edilkamin.js) — TypeScript implementation

## Support

For issues and feature requests, please open an issue on the [repository](https://github.com/StopThePcap/edilkamin-ble/issues).
