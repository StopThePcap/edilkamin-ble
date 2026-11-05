#!/bin/bash
# Edilkamin Stove BLE Pairing Script (manual fallback)
#
# The add-on pairs by itself (and re-pairs via "Pair Stove" in Home
# Assistant). Use this script only if that is not an option.
#
# Usage (on the Home Assistant host):
#   1. Stop the Edilkamin BLE add-on. The stove accepts one connection at a
#      time, and the add-on would compete with this script for it.
#   2. Run: ./pair_stove.sh [MAC]
#   3. The script keeps retrying until the stove accepts. If it takes more
#      than a minute, power-cycle the stove.
#   4. Start the add-on again.

set -u

WINDOW=180   # seconds to keep trying
MAC="${1:-}"

echo "========================================"
echo "Edilkamin BLE Pairing Script"
echo "========================================"

if ! command -v bluetoothctl &> /dev/null; then
    echo "Error: bluetoothctl not found. Is BlueZ installed?"
    exit 1
fi

if ! bluetoothctl list | grep -q "Controller"; then
    echo "Error: no Bluetooth adapter found (bluetoothctl list is empty)."
    exit 1
fi

bluetoothctl power on > /dev/null

# Keep a discovery session running for the whole window, so the stove shows up
# the moment it starts advertising after the power-cycle.
bluetoothctl --timeout "$WINDOW" scan le > /dev/null 2>&1 &
SCAN_PID=$!
trap 'kill $SCAN_PID 2> /dev/null' EXIT

if [ -z "$MAC" ]; then
    echo "Scanning for a device named EDILKAMIN_* ..."
    for _ in $(seq 1 15); do
        MAC=$(bluetoothctl devices | grep "EDILKAMIN_" | grep -oE '([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}' | head -1)
        [ -n "$MAC" ] && break
        sleep 2
    done
    if [ -z "$MAC" ]; then
        echo "Error: no EDILKAMIN_* device seen. Pass the MAC address as an argument."
        exit 1
    fi
fi
echo "Stove: $MAC"

# A stale bond makes 'pair' answer "AlreadyExists" without doing anything, so
# the old record has to go first.
if bluetoothctl info "$MAC" 2> /dev/null | grep -q "Paired: yes"; then
    echo "Removing the existing pairing for $MAC ..."
    bluetoothctl remove "$MAC" > /dev/null
fi

echo ""
echo ">>> Trying to pair for ${WINDOW}s (power-cycle the stove if it takes long) ..."
echo ""

DEADLINE=$(( $(date +%s) + WINDOW ))
ATTEMPT=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    if ! bluetoothctl info "$MAC" > /dev/null 2>&1; then
        # Not seen since the removal / power-cycle yet.
        sleep 1
        continue
    fi
    ATTEMPT=$((ATTEMPT + 1))
    # Even "Just Works" pairing needs an agent to confirm the request: without
    # one, BlueZ refuses it and the stove reports "Authentication Failed". A
    # one-shot `bluetoothctl pair` cannot answer the prompt, so drive an
    # interactive session and keep answering "yes".
    OUTPUT=$( { echo "agent NoInputNoOutput"; echo "default-agent"; echo "pair $MAC";
                for _ in 1 2 3 4 5 6 7 8 9 10; do sleep 2; echo "yes"; done;
                echo "quit"; } | timeout 30 bluetoothctl 2>&1 )
    if echo "$OUTPUT" | grep -q "Pairing successful"; then
        bluetoothctl trust "$MAC" > /dev/null
        bluetoothctl disconnect "$MAC" > /dev/null 2>&1
        echo "Pairing successful after $ATTEMPT attempt(s)."
        bluetoothctl info "$MAC" | grep -E "Name|Paired|Bonded|Trusted"
        echo ""
        echo "Start the add-on again. ble_mac_address: \"$MAC\""
        exit 0
    fi
    echo "  attempt $ATTEMPT: $(echo "$OUTPUT" | grep -iE 'fail|error' | tail -1)"
    sleep 1
done

echo ""
echo "Pairing did not succeed within ${WINDOW}s."
echo "Make sure the Edilkamin phone app is closed, then try again."
exit 1
