"""Constants for Edilkamin BLE communication."""

# BLE Service and Characteristics
BLE_SERVICE_UUID = "0000abf0-0000-1000-8000-00805f9b34fb"
BLE_WRITE_CHAR_UUID = "0000abf1-0000-1000-8000-00805f9b34fb"
BLE_NOTIFY_CHAR_UUID = "0000abf2-0000-1000-8000-00805f9b34fb"

# Device name pattern for discovery
DEVICE_NAME_PREFIX = "EDILKAMIN_"

# AES-128-CBC Encryption Key
AES_KEY = bytes([
    0x80, 0x29, 0x47, 0x46, 0xDB, 0x35, 0x4D, 0xB7,
    0x4C, 0x37, 0x01, 0xCF, 0x30, 0xEF, 0xDD, 0x65
])

# AES-128-CBC Initialization Vector (static, reused for every operation)
AES_IV = bytes([
    0xDA, 0x1A, 0x55, 0x73, 0x49, 0xF2, 0x5C, 0x64,
    0x1B, 0x1A, 0x21, 0xD2, 0x6F, 0x5B, 0x21, 0x8A
])

# Fixed key used in datagram payload (same as AES key in this protocol)
FIXED_KEY = bytes([
    0x31, 0xdd, 0x34, 0x51, 0x26, 0x39, 0x20, 0x23,
    0x9f, 0x4b, 0x68, 0x20, 0xe7, 0x25, 0xfc, 0x75
])

# Padding bytes for outbound packets
PADDING_OUT = bytes([0x04, 0x04, 0x04, 0x04])

# Modbus constants
MODBUS_SLAVE_ID = 0x01
MODBUS_READ_HOLDING_REGISTERS = 0x03
MODBUS_WRITE_SINGLE_REGISTER = 0x06

# ============================================================================
# READ REGISTERS - Format: (slave_id, function_code, reg_hi, reg_lo, len_hi, len_lo)
# ============================================================================

# Temperature registers
REG_READ_BOILER_SET = bytes([1, 3, 2, 238, 0, 1])
REG_READ_BOILER_TEMP = bytes([1, 3, 2, 181, 0, 1])
REG_READ_THERMOCOUPLE_TEMP = bytes([1, 3, 2, 187, 0, 1])  # Exhaust/flue gas temp
REG_READ_PUFFER_TEMP = bytes([1, 3, 2, 182, 0, 1])
REG_READ_SANI_BOILER_BOTTOM_TEMP = bytes([1, 3, 2, 184, 0, 1])
REG_READ_BOILER_BOTTOM_TEMP = bytes([1, 3, 2, 186, 0, 1])
REG_READ_MAIN_ENV_TEMP = bytes([1, 3, 2, 233, 0, 1])  # Room temperature

# Status registers
REG_READ_STOVE_TYPE = bytes([1, 3, 5, 221, 0, 1])
REG_READ_ALARM = bytes([1, 3, 2, 230, 0, 1])
REG_READ_TOTAL_ALARMS = bytes([1, 3, 3, 58, 0, 1])
REG_READ_FIREPLACE_STOVE_STATUS = bytes([1, 3, 2, 229, 0, 1])
REG_READ_FIREPLACE_MAIN_STATUS = bytes([1, 3, 2, 228, 0, 1])
REG_READ_FIREPLACE_CURRENT_POWER = bytes([1, 3, 2, 231, 0, 1])
REG_READ_WARNING_FLAGS = bytes([1, 3, 2, 244, 0, 2])

# Control state registers
REG_READ_AUTOMATIC = bytes([1, 3, 5, 35, 0, 1])
REG_READ_TEMPERATURE = bytes([1, 3, 5, 37, 0, 1])  # Target temperature
REG_READ_POWER = bytes([1, 3, 5, 41, 0, 1])  # Power level
REG_READ_FAN = bytes([1, 3, 5, 41, 0, 1])  # Fan 1 speed
REG_READ_FAN_23 = bytes([1, 3, 5, 42, 0, 1])  # Fan 2/3 speed
REG_READ_FAN_MACHINE = bytes([1, 3, 2, 231, 0, 1])
REG_READ_FAN_23_MACHINE = bytes([1, 3, 2, 232, 0, 1])
REG_READ_RELAX = bytes([1, 3, 5, 43, 0, 1])
REG_READ_NUMBER_FAN = bytes([1, 3, 5, 72, 0, 1])
REG_READ_FAN_1_2_ENV = bytes([1, 3, 5, 73, 0, 1])
REG_READ_FAN_3_ENV = bytes([1, 3, 5, 74, 0, 1])

# Pellet registers
REG_READ_PELLET_SENSOR = bytes([1, 3, 5, 46, 0, 1])
REG_READ_PELLET_REMAINING = bytes([1, 3, 2, 236, 0, 1])

# Chrono/timer registers
REG_READ_ON_OFF_CHRONO = bytes([1, 3, 5, 34, 0, 1])
REG_READ_ECO_TEMP = bytes([1, 3, 4, 204, 0, 1])
REG_READ_COMFORT_TEMP = bytes([1, 3, 4, 205, 0, 1])
REG_READ_CHRONO_DATA = bytes([1, 3, 4, 206, 0, 42])
REG_READ_CHRONO_POWER_DATA = bytes([1, 3, 4, 248, 0, 42])

# Standby registers
REG_READ_STANDBY_STATUS = bytes([1, 3, 5, 44, 0, 1])
REG_READ_STANDBY_START_TIME = bytes([1, 3, 2, 252, 0, 1])
REG_READ_STANDBY_TIMER = bytes([1, 3, 5, 45, 0, 1])

# Time registers
REG_READ_DATE_TIME = bytes([1, 3, 3, 22, 0, 4])
REG_READ_WEB_TIME = bytes([1, 3, 5, 56, 0, 1])
REG_READ_EASY_TIMER_TIME = bytes([1, 3, 3, 27, 0, 1])

# Canalized (ducted) registers
REG_READ_CANALIZED_2 = bytes([1, 3, 2, 234, 0, 1])
REG_READ_CANALIZED_3 = bytes([1, 3, 2, 235, 0, 1])
REG_READ_TEMP_CANALIZED_2 = bytes([1, 3, 5, 38, 0, 1])
REG_READ_TEMP_CANALIZED_3 = bytes([1, 3, 2, 39, 0, 1])

# Other registers
REG_READ_FUMES_TEMP_OFF = bytes([1, 3, 5, 168, 0, 1])
REG_READ_FIREPLACE_COMMANDS = bytes([1, 3, 3, 28, 0, 1])
REG_READ_STATS = bytes([1, 3, 2, 194, 0, 1])
REG_READ_DATA = bytes([1, 3, 3, 52, 0, 6])
REG_READ_TEMP_FORMAT = bytes([1, 3, 5, 36, 0, 1])
REG_READ_DISPLAY_IDRO = bytes([1, 3, 0, 150, 0, 8])

# Device info registers
REG_READ_MOTHERBOARD_SERIAL = bytes([1, 3, 0, 10, 0, 8])
REG_READ_MOTHERBOARD_NAME = bytes([1, 3, 0, 18, 0, 8])
REG_READ_MOTHERBOARD_BOOTLOADER = bytes([1, 3, 0, 26, 0, 8])
REG_READ_MOTHERBOARD_BL_VERSION = bytes([1, 3, 0, 34, 0, 8])
REG_READ_MOTHERBOARD_APP = bytes([1, 3, 0, 42, 0, 8])
REG_READ_MOTHERBOARD_APP_VERSION = bytes([1, 3, 0, 50, 0, 8])
REG_READ_REMOTE_CONTROL_SERIAL = bytes([1, 3, 0, 60, 0, 8])
REG_READ_REMOTE_CONTROL_NAME = bytes([1, 3, 0, 118, 0, 8])
REG_READ_EMERGENCY_PANEL_VERSION = bytes([1, 3, 0, 100, 0, 8])
REG_READ_COMMAND_VERSION = bytes([1, 3, 0, 150, 0, 8])
REG_READ_COMMAND_EMERGENCY_VERSION = bytes([1, 3, 2, 38, 0, 8])

# Alarm log registers
REG_READ_ALARM_LOGS_1 = bytes([1, 3, 3, 60, 0, 100])
REG_READ_ALARM_LOGS_2 = bytes([1, 3, 3, 160, 0, 100])
REG_READ_ALARM_LOGS_3 = bytes([1, 3, 4, 4, 0, 100])
REG_READ_ALARM_LOGS_4 = bytes([1, 3, 4, 104, 0, 100])

# ============================================================================
# WRITE REGISTERS - Format: (slave_id, function_code, reg_hi, reg_lo, val_hi, val_lo)
# B5 = val_hi, B6 = val_lo in the original code
# ============================================================================

# Base write commands (val_hi and val_lo to be filled)
REG_SET_ON_OFF = bytes([1, 6, 3, 28])  # B5=0, B6=1=on, 0=off
REG_SET_WRITE_NEW_POWER = bytes([1, 6, 5, 41])  # B5=fan1Value, B6=PowerLevel
REG_SET_WRITE_NEW_TEMP = bytes([1, 6, 5, 37])  # B4=37/38/39, B5=?, B6=?
REG_SET_FAN_1 = bytes([1, 6, 5, 41])  # B5=Power 0-6, B6=PowerLevel
REG_SET_FAN_2 = bytes([1, 6, 5, 42])  # B5=?, B6=0/1
REG_SET_FAN_3 = bytes([1, 6, 5, 43])  # B5=?, B6=0/1
REG_SET_CHANGE_AUTO_SWITCH = bytes([1, 6, 5, 35])  # B5=0/1=off/on
REG_SET_RELAX_STATUS = bytes([1, 6, 5, 43])  # B5=0/1, B6=0/1 (off=00, on=11)
REG_SET_ON_OFF_CHRONO = bytes([1, 6, 5, 34])  # B5=ChronoProgram, B6=0/1=on/off
REG_SET_STANDBY_STATUS = bytes([1, 6, 5, 44])  # B5=0/1
REG_SET_STANDBY_TIME = bytes([1, 6, 5, 45])  # B5=0/1, B6=0/1
REG_SET_TEMP_FORMAT = bytes([1, 6, 5, 36])  # B5=?, B6=0/1
REG_SET_TEMP_ECO = bytes([1, 6, 4, 204])  # B5=0/1, B6=2/3
REG_SET_TEMP_COMFORT = bytes([1, 6, 4, 205])  # B5=0/1, B6=2/3
REG_SET_CANALIZED_TEMP = bytes([1, 6, 5, 38])  # B4=38/39, B5=0/1, B6=2/3
REG_SET_EASY_TIME = bytes([1, 6, 3, 27])  # B5=0/1, B6=2/3
REG_SET_AIRKARE = bytes([1, 6, 3, 28])  # B5=0?, B6=0?
REG_SET_CONTINUE_COCLEA = bytes([1, 6, 5, 46])  # B5=0/1, B6=0/1
REG_SET_BLE_REGISTER = bytes([1, 6, 5, 56])  # B6=unknown
REG_SET_CHARGE_COCLEA = bytes([1, 6, 3, 28])  # B5=?, B6=0/1
REG_SET_SWEEP = bytes([1, 6, 3, 28])

# ============================================================================
# STATUS VALUE MAPPINGS
# ============================================================================

# Stove status register 0x02E4. The 16-bit value is (sub_phase << 8) | phase,
# i.e. payload[3] = sub-phase, payload[4] = operational phase. Names follow the
# official Edilkamin Android app (StatusFireplaceEnum), as published by
# github.com/AndreMiras/edilkamin.js (src/types.ts). The sub-phase is defined
# only during ignition; other phases report varying sub-values, e.g. (4, 2)
# while burning normally.
OPERATIONAL_PHASES = {
    0: "off",  # SPENTO
    1: "ignition",  # ACCENSIONE (see IGNITION_SUB_PHASES)
    2: "running",  # ACCESO
    3: "shutdown",  # SPEGNIMENTO
    4: "cooling",  # RAFFREDDAMENTO
    5: "final_cleaning",  # PULIZIA_FINALE
}
IGNITION_SUB_PHASES = {
    0: "cleaning",  # PULIZIA_STUFA_CALDA (hot stove cleaning)
    1: "cleaning",  # PULIZIA_SENZA_EKLEANER
    2: "cleaning",  # PULIZIA_CON_EKLEANER
    3: "loading_pellets",  # CARICO_PELLET
    4: "loading_pause",  # PAUSA_CARICAMENTO
    5: "checking_exhaust",  # CONTROLLO_INCREMENTO_TEMPERATURA
    6: "threshold_check",  # CONTROLLO_SUPERAMENTO_SOGLIA
    7: "warmup",  # WARMUP
}

# Alarm register 0x02E6 (low byte). From AlarmsEnum in the official Android
# app (1.3.1-RC2), as published by edilkamin.js.
ALARM_DESCRIPTIONS = {
    0: "No alarm",
    1: "Flue/chimney blocked",
    2: "Smoke extractor failure",
    3: "Pellet tank empty",
    4: "Ignition failed",
    5: "Unnamed alarm",
    6: "Smoke probe failure",
    7: "Exhaust overtemperature",
    8: "Pellet tank safety thermostat",
    9: "Auger motor failure",
    10: "Control board overtemperature",
    11: "Safety pressure switch",
    12: "Room probe failure",
    13: "Room probe 2 or boiler probe failure",
    14: "Room probe 3 or boiler probe failure",
    15: "Water/earthquake safety thermostat",
    16: "Water pressure transducer failure",
    17: "Outside or storage probe failure",
    18: "Buffer tank probe failure",
    19: "Crucible cleaning failure",
    20: "Power control (TRIAC) failure",
    21: "Power outage",
    22: "Door open",
    23: "Panel overtemperature",
    24: "Control board fuse",
}

# Power levels
POWER_LEVELS = {
    1: "P1",
    2: "P2",
    3: "P3",
    4: "P4",
    5: "P5",
}

# Fan modes
FAN_MODES = {
    1: "20%",
    2: "40%",
    3: "60%",
    4: "80%",
    5: "100%",
    6: "auto",
}

# Pellet level states
PELLET_LEVEL = {
    0: "ok",
    4: "empty",
}

# ============================================================================
# MQTT TOPICS
# ============================================================================

MQTT_BASE_TOPIC = "edilkamin"
MQTT_DISCOVERY_PREFIX = "homeassistant"

# Default configuration
DEFAULT_POLL_INTERVAL = 30  # seconds
DEFAULT_RECONNECT_DELAY = 5  # seconds
DEFAULT_COMMAND_TIMEOUT = 10  # seconds

# BLE timing
BLE_SCAN_TIMEOUT = 8.0  # seconds to look for the stove before a connect attempt
BLE_CONNECT_TIMEOUT = 30.0  # seconds for the GATT connection itself

# How long the stove may be unreachable before its entities are marked
# unavailable. Short reconnects keep the last values instead of flooding Home
# Assistant's logbook with "became unavailable".
AVAILABILITY_GRACE = 180  # seconds

# Reconnect backoff: delay = min(DEFAULT_RECONNECT_DELAY * 2**(n-1), MAX_BACKOFF)
MAX_BACKOFF = 300  # seconds

# Consecutive handshake drops (with no authentication reason from BlueZ) before
# the bond is considered stale and automatically re-paired.
HANDSHAKE_DROPS_BEFORE_REPAIR_HINT = 3

# Pairing mode: how long to keep trying, per-attempt timeouts, and the pause
# between failed attempts. The stove accepts pairing only briefly after it
# powers up, so the window must comfortably cover a power-cycle and boot.
PAIRING_WINDOW = 180.0  # seconds
PAIRING_SCAN_TIMEOUT = 3.0  # seconds per lookup while waiting for the stove
PAIRING_CONNECT_TIMEOUT = 20.0  # seconds for connect + pair
PAIRING_RETRY_DELAY = 1.0  # seconds

# Automatic re-pairing when the stove rejects our key ("PIN or Key Missing").
# The window is short because no user action is involved. The cooldown applies
# only after a *failed* automatic re-pair, so a stove that refuses pairing
# cannot pin the add-on in pairing mode.
AUTO_REPAIR_WINDOW = 60.0  # seconds
AUTO_REPAIR_COOLDOWN = 300.0  # seconds

# Values of the "Bluetooth Status" sensor (an enum sensor; Home Assistant shows
# these strings as they are, so they are written for people)
BT_STATUS_CONNECTING = "Connecting"
BT_STATUS_CONNECTED = "Connected"
BT_STATUS_DISCONNECTED = "Disconnected"
BT_STATUS_NO_ADAPTER = "No adapter"
BT_STATUS_NOT_PAIRED = "Not paired"  # no bond; press "Pair Stove"
BT_STATUS_PAIRING_REQUIRED = "Pairing required"  # stove rejects our bond
BT_STATUS_PAIRING = "Pairing"  # pairing mode active
BT_STATUS_PAIRING_FAILED = "Pairing failed"
BT_STATUSES = (
    BT_STATUS_CONNECTING,
    BT_STATUS_CONNECTED,
    BT_STATUS_DISCONNECTED,
    BT_STATUS_NO_ADAPTER,
    BT_STATUS_NOT_PAIRED,
    BT_STATUS_PAIRING_REQUIRED,
    BT_STATUS_PAIRING,
    BT_STATUS_PAIRING_FAILED,
)

# Display text for each StovePhase value, published on the "Phase" enum
# sensor. Must cover every StovePhase, or Home Assistant rejects the state.
PHASE_LABELS = {
    "off": "Off",
    "ignition": "Igniting",
    "cleaning": "Cleaning",
    "loading_pellets": "Loading pellets",
    "loading_pause": "Loading pause",
    "checking_exhaust": "Checking exhaust",
    "threshold_check": "Reaching temperature",
    "warmup": "Warming up",
    "running": "Running",
    "shutdown": "Shutting down",
    "cooling": "Cooling down",
    "final_cleaning": "Final cleaning",
    "unknown": "Unknown",
}

# Time to let the stove apply a write before the state is re-read
COMMAND_SETTLE_DELAY = 1.0  # seconds

# MQTT
MQTT_CONNECT_TIMEOUT = 30.0  # seconds to wait for the initial broker connection
