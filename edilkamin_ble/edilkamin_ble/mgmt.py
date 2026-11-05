"""Per-device LE connection parameters via the kernel's Bluetooth mgmt API.

Linux opens LE links with a 420 ms supervision timeout by default: miss
packets for less than half a second and the link is declared dead
(disconnect reason 0x08). At the stove's typical signal (-70 to -85 dBm)
that killed the connection within seconds of every connect. With 20 s, the
same link was observed holding steadily.

BlueZ offers no D-Bus API for this, so the parameters are loaded directly
with MGMT_OP_LOAD_CONN_PARAM. That needs CAP_NET_ADMIN (``privileged:
NET_ADMIN`` in config.yaml). Without it this is a logged no-op. Removing the
device (re-pairing) drops stored parameters, so callers apply them before
every connect.
"""

from __future__ import annotations

import ctypes
import logging
import os
import select
import struct
import sys
import time

_LOGGER = logging.getLogger(__name__)

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_CONTROL = 3
HCI_DEV_NONE = 0xFFFF

MGMT_OP_LOAD_CONN_PARAM = 0x0035
MGMT_EV_CMD_COMPLETE = 0x0001
MGMT_EV_CMD_STATUS = 0x0002
BDADDR_LE_PUBLIC = 0x01

# Units: interval 1.25 ms, timeout 10 ms.
CONN_MIN_INTERVAL = 24  # 30 ms (kernel default)
CONN_MAX_INTERVAL = 40  # 50 ms (kernel default)
CONN_LATENCY = 0
SUPERVISION_TIMEOUT = 2000  # 20 s (kernel default 42 = 420 ms)


class _SockaddrHCI(ctypes.Structure):
    _fields_ = [
        ("family", ctypes.c_ushort),
        ("dev", ctypes.c_ushort),
        ("channel", ctypes.c_ushort),
    ]


_warned = False


def load_conn_params(adapter_path: str, mac: str) -> bool:
    """Store robust connection parameters for ``mac`` on the given adapter.

    Args:
        adapter_path: BlueZ adapter object path, e.g. "/org/bluez/hci0".
        mac: Device address, "AA:BB:CC:DD:EE:FF" (LE public).

    Returns:
        True if the kernel accepted the parameters.
    """
    global _warned
    if sys.platform != "linux":
        return False

    try:
        index = int(adapter_path.rsplit("hci", 1)[1])
    except (IndexError, ValueError):
        _LOGGER.debug("Cannot derive HCI index from %s", adapter_path)
        return False

    addr = bytes(reversed(bytes.fromhex(mac.replace(":", ""))))
    params = struct.pack("<H", 1) + addr + struct.pack(
        "<BHHHH",
        BDADDR_LE_PUBLIC,
        CONN_MIN_INTERVAL,
        CONN_MAX_INTERVAL,
        CONN_LATENCY,
        SUPERVISION_TIMEOUT,
    )
    packet = struct.pack("<HHH", MGMT_OP_LOAD_CONN_PARAM, index, len(params)) + params

    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(AF_BLUETOOTH, 3 | os.O_CLOEXEC, BTPROTO_HCI)  # SOCK_RAW
    if fd < 0:
        _LOGGER.debug("mgmt socket failed: errno %d", ctypes.get_errno())
        return False
    try:
        addr_hci = _SockaddrHCI(AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_CONTROL)
        if libc.bind(fd, ctypes.byref(addr_hci), ctypes.sizeof(addr_hci)) < 0:
            _LOGGER.debug("mgmt bind failed: errno %d", ctypes.get_errno())
            return False
        os.write(fd, packet)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            reply = os.read(fd, 512)
            if len(reply) < 9:
                continue
            event, _ev_index, _length, opcode, status = struct.unpack_from("<HHHHB", reply)
            if event not in (MGMT_EV_CMD_COMPLETE, MGMT_EV_CMD_STATUS):
                continue
            if opcode != MGMT_OP_LOAD_CONN_PARAM:
                continue
            if status == 0:
                _LOGGER.debug(
                    "Loaded connection parameters for %s (supervision timeout %d ms)",
                    mac,
                    SUPERVISION_TIMEOUT * 10,
                )
                return True
            if status == 0x14 and not _warned:  # MGMT_STATUS_PERMISSION_DENIED
                _warned = True
                _LOGGER.warning(
                    "Could not set Bluetooth connection parameters (the add-on "
                    "lacks NET_ADMIN). On a weak signal the link may drop "
                    "every few seconds."
                )
            else:
                _LOGGER.debug("Load connection parameters failed: status 0x%02x", status)
            return False
        _LOGGER.debug("No reply to Load Connection Parameters")
        return False
    finally:
        os.close(fd)
