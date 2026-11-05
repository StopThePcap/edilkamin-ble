# Edilkamin BLE Add-on — Technical Rundown

## Overview

This Home Assistant add-on provides local Bluetooth Low Energy (BLE) control for Edilkamin pellet stoves equipped with the "The Mind" WiFi/Bluetooth module. It runs as a Docker container on the HA host, communicating directly with the stove over BLE and exposing entities to Home Assistant via MQTT Discovery.

```
┌──────────────┐    BLE     ┌────────────────┐    MQTT    ┌─────────────────┐
│  Edilkamin   │◄──────────►│  Add-on        │◄──────────►│  Home Assistant  │
│  Stove       │  AES-CBC   │  (Docker)      │  Discovery │  + Mosquitto    │
│  "The Mind"  │  Modbus    │                │            │                 │
└──────────────┘            └────────────────┘            └─────────────────┘
```

## Architecture

The add-on is structured into seven modules:

| Module | Role |
|--------|------|
| `__main__.py` | Orchestrator — owns the poll loop, reconnect backoff, availability and shutdown |
| `ble_client.py` | BLE transport — locate, connect, pair, send/receive 32-byte packets |
| `protocol.py` | Packet encoding — builds Modbus-over-BLE datagrams, CRC, encryption, response validation |
| `crypto.py` | AES-128-CBC encrypt/decrypt with static key and IV |
| `stove.py` | Stove abstraction — maps registers to meaningful state (temperature, phase, etc.) |
| `mqtt_handler.py` | MQTT transport — broker connection, state publishing, command dispatch |
| `mqtt_discovery.py` | MQTT Discovery payloads and the state/command topic map |

### Data Flow

**Polling (every 30s, or sooner after a command):**
```
__main__.py poll loop
  → stove.py poll_status()          (holds the stove operation lock)
    → protocol.py build_read_command(register)
      → crypto.py encrypt(plaintext)
    → ble_client.py send_command(encrypted_packet)   (holds the BLE command lock)
      → BLE write to characteristic ABF1
      → BLE notification from ABF2
    → protocol.py parse_response(encrypted_response)
      → crypto.py decrypt(ciphertext)
    → protocol.py validate_read_response(response, register)
  → mqtt_handler.py publish_state(stove_state)
    → MQTT publish to state topics
  → mqtt_handler.py publish_availability(True)
```

**Commands from HA (e.g., set temperature):**
```
MQTT message on command topic
  → mqtt_handler.py _on_message()          (paho network thread)
    → run_coroutine_threadsafe → CommandHandler._handle_command()
      → stove.py set_temperature()         (holds the stove operation lock)
        → protocol.py build_write_command(register, value)
          → crypto.py encrypt(plaintext)
        → ble_client.py send_command(encrypted_packet)
      → sleep(COMMAND_SETTLE_DELAY), then set the refresh event
  → poll loop wakes early, polls once, publishes state
```

The command path deliberately does **not** poll the stove itself. Several
commands arriving together (Home Assistant sends mode, preset and temperature
as separate messages) would otherwise each start their own full register sweep
over a link that handles one request at a time. Signalling the poll loop
collapses them into a single read.

### Concurrency and Serialisation

Only one BLE exchange may be in flight at a time. The stove answers over a
notification characteristic with nothing tying a reply to its request, so two
overlapping exchanges will hand each caller the other's response — which shows
up as plausible-but-wrong readings rather than an error. Two locks enforce this:

| Lock | Scope | Purpose |
|------|-------|---------|
| `EdilkaminBLEClient._command_lock` | One write + its response | Nothing can interleave between a write and the notification that answers it |
| `EdilkaminStove._op_lock` | A whole poll, or a whole control action | Stops a command landing in the middle of a status sweep |

`EdilkaminBLEClient._connection_lock` separately guards `connect()`/`disconnect()`.

Three further defences catch a desync if one ever occurs:

- The response queue is drained before each command, on disconnect, and on
  connect — anything buffered belongs to an abandoned exchange.
- `validate_read_response()` rejects a reply whose slave id and function code
  do not match the request that produced it.
- A command timeout or a failed GATT write clears the connected flag, so the
  poll loop rebuilds the link instead of retrying against a stuck one.

## BLE Protocol

### Service and Characteristics

The stove advertises as `EDILKAMIN_EP` with BLE service `0000abf0-0000-1000-8000-00805f9b34fb`.

| Characteristic | UUID | Properties | Purpose |
|---------------|------|------------|---------|
| ABF1 | `0000abf1-...` | write-without-response, read | Send commands to stove |
| ABF2 | `0000abf2-...` | notify, read | Receive responses from stove |
| ABF3 | `0000abf3-...` | write-without-response, read | Unknown (10-byte, unused) |
| ABF4 | `0000abf4-...` | notify, read | Unknown (10-byte, unused) |

Communication uses ABF1 (write) and ABF2 (notify). ABF3/ABF4 exist but their purpose is undocumented — writing to ABF3 causes the stove to disconnect.

### Packet Structure

Every message is a 32-byte datagram, encrypted with AES-128-CBC.

**Outbound (command) — 32 bytes plaintext:**

```
Offset  Length  Content
───────────────────────────────────
0-3     4       Unix timestamp (big-endian)
4-19    16      Fixed key (static authentication token)
20-25   6       Modbus RTU command
26-27   2       CRC16-Modbus of bytes 20-25 (little-endian)
28-31   4       Padding: 0x04 0x04 0x04 0x04
```

**Inbound (response) — 32 bytes plaintext:**

```
Offset  Length  Content
───────────────────────────────────
0-3     4       Timestamp
4-19    16      Fixed key
20-26   7       Modbus RTU response
27-28   2       CRC16-Modbus
29-31   3       Padding: 0x03 0x03 0x03
```

> **Caveat — the response tail is inferred, not captured.** A Modbus RTU read
> of one register is 5 PDU bytes plus 2 CRC bytes, which already fills offsets
> 20-26, leaving no room for a second CRC at 27-28. Either the trailing bytes
> are PKCS#7-style padding (outbound uses `04 04 04 04`, i.e. four bytes of
> value 4, so a five-byte tail would read `05 05 05 05 05`) or the frame
> carries more payload than a single-register read needs.
>
> Only offsets 20-24 are actually relied upon, and those are confirmed by
> working temperature and status readings. The add-on therefore validates the
> response **header** (slave id, function code, byte count) and does not verify
> a response CRC — enforcing a checksum whose position is unconfirmed would
> reject every valid reply. Resolving this needs a capture from real hardware.

### Encryption

| Parameter | Value |
|-----------|-------|
| Algorithm | AES-128-CBC |
| Key | `80 29 47 46 DB 35 4D B7 4C 37 01 CF 30 EF DD 65` |
| IV | `DA 1A 55 73 49 F2 5C 64 1B 1A 21 D2 6F 5B 21 8A` |

Both key and IV are static — the same values are used for every encrypt and decrypt operation. The IV is never rotated. A new `AES.new()` cipher object is created for each operation because CBC mode is stateful.

The **fixed key** embedded in every packet (`31 DD 34 51 26 39 20 23 9F 4B 68 20 E7 25 FC 75`) is NOT the encryption key — it's a static authentication token the stove checks after decryption.

### Modbus RTU Commands

The protocol wraps standard Modbus RTU inside the encrypted BLE packets. Two function codes are used:

- **0x03** — Read Holding Registers
- **0x06** — Write Single Register

**Read command format (6 bytes):**
```
[slave_id, function_code, register_hi, register_lo, length_hi, length_lo]
Example: 01 03 02 E4 00 01  (read main status register 0x02E4, 1 word)
```

**Read response format (7 bytes):**
```
[slave_id, function_code, byte_count, data_hi, data_lo, ...]
Example: 01 03 02 00 00 ...  (status bytes: 0x00, 0x00 = Off)
```

`validate_read_response()` checks the first three bytes against the request
before the data is used: the slave id and function code must match, and the
byte count must be 2×(registers requested) whenever that many bytes could fit
in the datagram. A stray notification, a write echo (function 0x06) mistaken
for a read, or a failed decryption all fail this check and raise
`ProtocolError`, which the poll loop treats as a failed read and retries.

Writes are not validated this way — the response to function 0x06 is discarded,
so there is nothing to corrupt, and the echo format is unconfirmed.

**Write command format (6 bytes):**
```
[slave_id, function_code, register_hi, register_lo, value_hi, value_lo]
Example: 01 06 03 1C 00 01  (write 1 to register 0x031C = turn on)
```

### Key Registers

A full status poll is 11 round trips — one per row in the table below. Power
level and fan speed share register 0x0529 and are read together; reading them
separately would cost an extra round trip for the same two bytes.

**Read registers:**

| Register | Address | Returns |
|----------|---------|---------|
| Main Status | 0x02E4 | (sub_phase, phase) → stove phase |
| Room Temperature | 0x02E9 | (hi, lo) → temp × 10 in °C |
| Target Temperature | 0x0525 | (hi, lo) → temp × 10 in °C |
| Exhaust Temperature | 0x02BB | (hi, lo) → temp × 10 in °C |
| Power Level | 0x0529 | (fan_speed, power_level) |
| Alarm | 0x02E6 | (alarm_code, 0): code in the high byte; 0 = no alarm, see below |
| Pellet Sensor | 0x052E | (0, status) — 0=ok, 4=empty |
| Automatic Mode | 0x0523 | (0, enabled) — 0/1 |
| Relax Mode | 0x052B | (enabled, enabled) — both 1 = on |
| Chrono Mode | 0x0522 | (program, enabled) — 0/1 |
| Standby Mode | 0x052C | (0, enabled) — 0/1 |

**Stove phases.** The main status register holds `(sub_phase, phase)`.
Names come from the official Edilkamin Android app (`StatusFireplaceEnum`),
as published by [edilkamin.js](https://github.com/AndreMiras/edilkamin.js)
(`src/types.ts`).

| Phase (low byte) | Meaning |
|------------------|---------|
| 0 | Off |
| 1 | Ignition (see sub-phases) |
| 2 | On (running) |
| 3 | Shutting down |
| 4 | Cooling |
| 5 | Final cleaning |

| Ignition sub-phase (high byte) | Meaning |
|--------------------------------|---------|
| 0 / 1 / 2 | Cleaning (hot stove / without / with eKleaner) |
| 3 | Loading pellets |
| 4 | Loading pause |
| 5 | Checking exhaust temperature rise |
| 6 | Threshold check |
| 7 | Warm-up |

The sub-phase is documented only for ignition. While running, the stove
reports varying sub-values: `(2, 2)` and `(4, 2)` have both been seen, so the
add-on decodes the phase byte first rather than matching exact pairs (as
netmb/Edilkamin_BT does, where anything but `(2, 2)` is unknown). The raw codes are published as attributes of the
*Phase* sensor (`phase_code`, `sub_phase_code`).

**Alarm codes** (`AlarmsEnum`, same source), published with a description as
attributes of the *Alarm* sensor:

The code is in the **high** byte: after a power outage the register reads
`(21, 0)`. The add-on falls back to the low byte if the high byte is 0, in case
other firmware differs. While an alarm is latched the stove acknowledges power-on writes but
ignores them; the alarm has to be reset at the stove (no Bluetooth reset command
is known).

| Code | Alarm | Code | Alarm |
|------|-------|------|-------|
| 0 | None | 13 | Room probe 2 / boiler probe failure |
| 1 | Flue/chimney blocked | 14 | Room probe 3 / boiler probe failure |
| 2 | Smoke extractor failure | 15 | Water/earthquake safety thermostat |
| 3 | Pellet tank empty | 16 | Water pressure transducer failure |
| 4 | Ignition failed | 17 | Outside / storage probe failure |
| 5 | Unnamed alarm | 18 | Buffer tank probe failure |
| 6 | Smoke probe failure | 19 | Crucible cleaning failure |
| 7 | Exhaust overtemperature | 20 | Power control (TRIAC) failure |
| 8 | Pellet tank safety thermostat | 21 | Power outage |
| 9 | Auger motor failure | 22 | Door open |
| 10 | Control board overtemperature | 23 | Panel overtemperature |
| 11 | Safety pressure switch | 24 | Control board fuse |
| 12 | Room probe failure | | |

**Write registers:**

| Register | Address | Value |
|----------|---------|-------|
| Power On/Off | 0x031C | (0, 1)=on, (0, 0)=off |
| Temperature | 0x0525 | (temp_hi, temp_lo) — temp × 10 |
| Power Level | 0x0529 | (fan_speed, level) — level 1-5 |
| Fan Speed | 0x0529 | (speed, power_level) — speed 1-6 |
| Automatic Mode | 0x0523 | (enabled, enabled) — 0/1 ⚠️ |
| Relax Mode | 0x052B | (enabled, enabled) — 0/1 |
| Chrono Mode | 0x0522 | (program, enabled) — 0/1 |
| Standby Mode | 0x052C | (enabled, 0) — 0/1 ⚠️ |

> ⚠️ **Unverified encodings.** Modbus function 0x06 writes a single 16-bit
> value, so `(1, 1)` sends 0x0101 = 257 and `(1, 0)` sends 0x0100 = 256 —
> whereas Power On/Off sends a plain 1. Automatic Mode and Standby Mode are
> therefore likely wrong, but the correct form cannot be confirmed without a
> capture from a real stove, and guessing risks breaking behaviour that may
> currently work. Both call sites are marked `UNVERIFIED` in `stove.py`.
>
> Relax Mode is left as-is deliberately: its read register genuinely reports
> `(enabled, enabled)`, so the symmetric write is plausible there.

## BLE Connection Lifecycle

### Pairing

The stove requires BLE bonding before it will accept encrypted commands.
Pairing is LE Secure Connections "Just Works": both sides advertise
NoInputNoOutput, and the stove itself sends a Security Request (Bonding | SC).
The bond is stored persistently and survives reboots of both HA and the stove.

**A pairing agent is required.** Current kernels ask userspace to confirm even
Just Works pairing. bluetoothd forwards that to the registered agent, and with
none it answers "no". The kernel then aborts with SMP *Pairing Failed:
Numeric Comparison Failed (0x0c)* right after the key exchange, which surfaces
as `org.bluez.Error.AuthenticationFailed`. Neither bleak nor Home Assistant
registers an agent. `bluez_agent.py` provides one that approves requests for
the configured stove only and rejects every other device. `BlueZ.pairing_agent()`
registers it as the default agent only around pairing attempts. Confirmed on
the wire with an HCI monitor capture: without an agent every attempt failed
this way; with it, the first attempt succeeded, with no stove power-cycle
needed.

On Linux all pairing goes through bleak (`BleakClient(..., pair=True)`, which
calls `Device1.Pair` on bleak's own D-Bus connection) on the same link that is
then used for GATT. Pairing with a one-shot `bluetoothctl pair` and then
opening a second connection can see that second link dropped right after a
successful pairing.

- **Normal connect:** if BlueZ holds no bond for the stove, pairing is
  attempted as part of the connection (agent registered). Failure raises
  `BLENotPairedError`.
- **Pairing mode** (`EdilkaminBLEClient.pair()`, started by the *Pair Stove*
  MQTT button): removes the BlueZ device record and bond (`RemoveDevice`),
  then for 180s repeatedly looks for the stove (3s lookups) and connects with
  `pair=True` as soon as it is seen, retrying until the stove accepts.

On macOS, CoreBluetooth handles bonding transparently.

### BlueZ state (`bluez.py`)

Adapter presence, the `Paired`/`Bonded`/`Connected` properties and the
`Device1.Disconnected(reason, message)` signal are read directly over the
system D-Bus with dbus-fast, which bleak already depends on. Scraping
`bluetoothctl info` instead is unreliable: any failure there (no controller,
timeout, unknown device) reads as "not paired".

### Connection Sequence

1. **Tear down** — detach and disconnect any previous `BleakClient` *before*
   arming handshake-drop detection (see below)
2. **Locate** — `BleakScanner.find_device_by_address()` with an 8s timeout,
   falling back to a direct connect by MAC if the stove is not advertising.
   A targeted lookup is used rather than a full `discover()` sweep, because an
   active scan of the whole neighbourhood competes with Home Assistant's own
   Bluetooth integration on the same adapter
3. **Prepare** — on Linux: read the BlueZ state over D-Bus. No powered adapter
   → `BLEAdapterUnavailableError` straight away, with no scan, pair or connect
   cascade. A leftover BlueZ connection is dropped. No bond → pair during
   connect
4. **Connect** — `BleakClient.connect()` with 30s timeout (and `pair=True`
   when there is no bond)
5. **Verify** — enumerate GATT services, confirm ABF0 service exists
6. **Subscribe** — `start_notify()` on ABF2 characteristic
7. **Poll** — enter the polling loop

### Connection Parameters (`mgmt.py`)

Before every connect (and every pairing attempt), the add-on loads per-device LE
connection parameters with `MGMT_OP_LOAD_CONN_PARAM` on the kernel's mgmt
control channel: interval 30-50 ms, latency 0, **supervision timeout 20 s**.
The kernel default is 420 ms (`/sys/kernel/debug/bluetooth/hci0/supervision_timeout`
= 42). With the stove at -70 to -85 dBm, that expired within seconds of every
connect, seen on the air as Disconnect reason 0x08 (connection timeout). In
testing: 20 drops in 5 minutes at 420 ms, versus a single connection holding
for hours at 20 s.

BlueZ has no D-Bus API for this, and removing a device (re-pairing) also
deletes stored parameters, hence the per-connect call. The control channel
needs CAP_NET_ADMIN, granted with `privileged: [NET_ADMIN]` in config.yaml.
Without it the kernel answers "Permission Denied", which is logged once.

### Reconnection

The poll loop owns all reconnect scheduling; the BLE client makes exactly one
attempt per `connect()` call. On a failure:

1. Availability is published as `offline`
2. The delay is `min(5 × 2^(n-1), 300)` seconds for the *n*th consecutive
   failure — 5, 10, 20, 40, 80, 160, then 300s
3. The counter resets on the first successful poll

A failed poll additionally drops the link (`disconnect()`) so the next
iteration rebuilds it from scratch rather than reusing a half-dead connection.

Every sleep in the loop is interruptible by the shutdown event, so SIGTERM is
acted on immediately instead of after up to a minute.

### Failure Classification

A failed connect is turned into one of these, and the poll loop maps each one to
the *Bluetooth Status* sensor:

| Error | When | Status |
|-------|------|--------|
| `BLEAdapterUnavailableError` | No powered adapter, before *or after* the attempt | `No adapter` (backoff capped at 60s) |
| `BLENotPairedError` | No bond, and pairing during connect failed | `Not paired` |
| `BLEHandshakeDropError` | Bonded, but the link dropped during setup | `Pairing required` if BlueZ reports `org.bluez.Reason.Authentication` or after 3 in a row, else `Disconnected` |
| `BLEConnectionError` | Anything else | `Disconnected` |

**The bond is only replaced on specific evidence.** Removing it after any few
handshake drops is wrong: a USB dongle that crashes when a connection opens
produces exactly the same pattern (the link drops during setup, then the
adapter vanishes). So:

- BlueZ reason `org.bluez.Reason.Authentication`, **or** 3 consecutive handshake
  drops with the adapter present: the main loop runs pairing mode for 60s, which
  needs no user action thanks to the agent. It backs off for 5 minutes only
  after an automatic re-pair *failed*: the stove has been seen rejecting a key
  30 seconds after agreeing to it, so a cooldown after success would mean
  minutes offline for nothing.
  The 3-drop rule is needed because the stove answers a stale key with
  *PIN or Key Missing* (HCI 0x06) within about 0.6s. With some adapters (seen
  on a Barrot-based dongle) that happens before the kernel reports the link to BlueZ as
  connected, so BlueZ sees a failed connect and gives no reason. Meanwhile it
  keeps reconnecting by itself until bleak's timeout, roughly 40 times in 30s,
  so the last reason is our own `Local`. When a reason does arrive, the attempt
  is aborted at once (`_connect_unless_rejected`).
- A wrong guess (e.g. a weak link) costs one re-pair; an unpaired connect also
  pairs by itself.
- Observed in testing: the stove discarded a bond about 12 minutes after
  pairing (during ignition), and another about 30 seconds after pairing,
  following a poll that timed out. Both happened while the link kept dropping
  at the kernel's default supervision timeout; with the 20 s timeout, bonds
  have held for days. No phone app was involved. The cause is
  unknown (stove firmware): possibly its BLE module restarting and keeping bonds
  only in RAM.
- Bonding cannot be skipped: tested unpaired (adapter not pairable), the stove
  disconnects right after service discovery, before notifications can be
  enabled.
- Adapter compatibility: see "Tested Hardware" in DOCS.md. A Barrot-based
  USB dongle (USB 33fa:0010, HCI manufacturer 2279) could re-encrypt with an
  existing key but never got an SMP Pairing Request onto the air. The kernel
  logged "unexpected SMP command 0x0b" for each stove Security Request and
  "Unexpected continuation" on every start, so new pairings are impossible
  with it.

Handshake-drop detection is deliberately narrow:

- The `_disconnected_during_connect` flag is armed only between
  `BleakClient.connect()` and the end of connection setup. BlueZ fires
  `disconnected_callback` for our *own* `disconnect()` calls too, so a wider
  window would classify ordinary reconnects as handshake drops.
- The previous client is detached (`self._client = None`) before it is
  disconnected, and `_on_disconnect` ignores callbacks from any client that is
  no longer the current one.
- After a drop, the adapter is checked again before anything is blamed on the
  pairing.

## MQTT Integration

### Discovery

On startup the add-on publishes 16 MQTT discovery configs under the configured
discovery prefix (`homeassistant/` by default) with `retain=True`. Home
Assistant auto-discovers these entities:

| Entity Type | Entities |
|-------------|----------|
| Climate | Main thermostat control (modes, presets, fan, temperature) |
| Sensors | Current temperature, exhaust temperature, operating phase, power level, fan speed |
| Binary Sensors | Running, alarm, pellet tank empty |
| Switches | Power, relax mode, chrono mode, standby mode, automatic mode |
| Add-on | *Bluetooth Status* sensor (diagnostic), *Pair Stove* button (config) |

They are republished whenever the broker reconnects, since a broker restart
drops retained messages and would otherwise leave Home Assistant with no
entities at all.

**The discovery payloads must not set `retain`.** That option controls the
messages *Home Assistant* publishes to the command topics, not the discovery
messages themselves. With it set, every command HA ever sent stays retained on
the broker and is replayed the moment the add-on re-subscribes after a
reconnect — nine simultaneous writes, each historically triggering its own
full register sweep, at a device that handles one request at a time. For the
same reason the add-on subscribes to command topics in exactly one place
(`_on_connect`); a second subscribe re-triggers retained delivery.

### Topics

State topics: `edilkamin/{device_id}/{state_name}`
Command topics: `edilkamin/{device_id}/{command}/set`
Availability: `edilkamin/{device_id}/availability` (stove reachable) and
`edilkamin/{device_id}/bridge` (add-on running), both online/offline

### Availability

`availability` is published as `online` after **every** successful poll, not
just on connect. Publishing it only in the connect branch means any recovery
that does not pass through that branch leaves a retained `offline` on the
broker, and Home Assistant greys out every entity while the log shows healthy
polls.

`offline` is published only once the stove has been unreachable for 3 minutes
(`AVAILABILITY_GRACE`). A reconnect usually takes seconds. Marking every entity
unavailable for each one put a "became unavailable" between almost every real
entry in Home Assistant's logbook and history. During the grace period the
entities keep their last values. The *Bluetooth Status* sensor still shows the
link state immediately.

Stove entities require both `bridge` and `availability` (`availability_mode:
all`). The *Pair Stove* button and *Bluetooth Status* sensor depend on `bridge`
only, because they are needed exactly when the stove is unreachable.

The MQTT Last Will is registered on `bridge`, so a crash or a SIGKILL marks
every entity offline rather than leaving a retained `online` and stale values
behind indefinitely. `bridge` is republished as `online` on every broker
(re)connect, and as `offline` on a clean shutdown.

### Command Handling

When HA sends a command via MQTT (e.g., user toggles the power switch), the
`CommandHandler`:

1. Receives the MQTT message on the command topic (paho's network thread)
2. Hands it to the event loop via `run_coroutine_threadsafe`
3. Translates it to a stove write command (e.g., power ON → write register
   0x031C with value 0x0001) and sends the encrypted BLE packet, serialised
   behind the stove operation lock
4. Waits `COMMAND_SETTLE_DELAY` (1s) for the stove to apply it
5. Sets the refresh event

The poll loop then wakes early, polls once and publishes the result. The
handler is registered before the broker connection is opened, so a command
arriving the instant we subscribe is not dropped.

## Startup and Shutdown

`start()` connects to MQTT before touching BLE. The broker is often not up yet
when the add-on starts at boot, so the connection is retried with the same
exponential backoff rather than treated as fatal — exiting there would leave
the add-on dead until manually restarted. paho's own background retry keeps
running throughout, so subsequent attempts simply wait for it to succeed.

On SIGTERM/SIGINT the signal handler only sets flags (`request_stop()`). Every
sleep in the add-on waits on the shutdown event, so the loops unwind on their
own and teardown runs once, from a single place, publishing `offline` and
closing both connections. `stop()` is idempotent.

## Docker Container

The add-on runs as an Alpine Linux container with:
- `host_network: true` — required for BLE access
- `host_dbus: true` — required for BlueZ/D-Bus communication
- `privileged: [NET_ADMIN]`: needed to set per-device connection parameters
  over the kernel's Bluetooth mgmt socket (see Connection Parameters)
- Pairing, bond state and the pairing agent all go through the *host's*
  `bluetoothd` over the shared D-Bus. The image includes `bluez` only so that
  bleak's startup `bluetoothctl --version` check works; nothing else calls it
- Python dependencies: `bleak` (BLE), `dbus-fast` (direct BlueZ D-Bus access,
  also bleak's own backend), `paho-mqtt` (MQTT), `pycryptodome` (AES), each
  pinned to a major version so a rebuild cannot silently change BLE or MQTT
  behaviour under a running install

## Known Gaps

Items that need a capture from real hardware to resolve:

- The inbound packet tail (offsets 27-31) — see the caveat under Packet
  Structure. Response CRCs are consequently not verified.
- The write encoding for Automatic Mode and Standby Mode — see the warning
  under Key Registers.
- Whether ABF1 would accept write-*with*-response. It advertises
  write-without-response only, which means no flow control at the GATT layer;
  a lost write currently surfaces as a command timeout.

## Reference

Protocol based on reverse engineering from:
- [netmb/Edilkamin_BT](https://github.com/netmb/Edilkamin_BT) — ESP32 firmware (C++/NimBLE)
- [AndreMiras/edilkamin.js](https://github.com/AndreMiras/edilkamin.js) — TypeScript implementation
