"""BlueZ pairing agent that approves "Just Works" pairing with the stove only.

The stove pairs with LE Secure Connections, both sides NoInputNoOutput, which
is Just Works. Current kernels still ask userspace to confirm such a pairing,
and bluetoothd forwards that to a registered agent. With no agent it answers
"no", and the kernel aborts with SMP "Numeric Comparison Failed" right after
the key exchange. bleak registers no agent and neither does Home Assistant,
so without this, pairing from the add-on can never succeed.

Kept free of ``from __future__ import annotations``: dbus-fast reads the D-Bus
signature from the parameter annotations below.
"""

import logging

from dbus_fast import DBusError
from dbus_fast.service import ServiceInterface, method

_LOGGER = logging.getLogger(__name__)

AGENT_INTERFACE = "org.bluez.Agent1"
REJECTED = "org.bluez.Error.Rejected"


class StovePairingAgent(ServiceInterface):
    """Accept confirmation requests for one device, reject everything else."""

    def __init__(self, device_suffix: str):
        super().__init__(AGENT_INTERFACE)
        # e.g. "/dev_CC_DB_A7_D8_1E_9A". Matched as a suffix so the adapter
        # (hci0, hci1, ...) does not matter.
        self._device_suffix = device_suffix

    def _check(self, device: str, what: str) -> None:
        if device.endswith(self._device_suffix):
            _LOGGER.info("Approving %s for the stove", what)
            return
        # Every other device keeps the behaviour it had without this agent.
        _LOGGER.info("Rejecting %s for %s (not the stove)", what, device)
        raise DBusError(REJECTED, "Not the configured stove")

    @method()
    def Release(self):  # noqa: N802 - D-Bus method name
        pass

    @method()
    def RequestAuthorization(self, device: "o"):  # noqa: N802,F821
        # Just Works confirmation (kernel confirm_hint set).
        self._check(device, "Just Works pairing")

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):  # noqa: N802,F821
        self._check(device, "pairing confirmation")

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):  # noqa: N802,F821
        self._check(device, f"service {uuid}")

    @method()
    def RequestPinCode(self, device: "o") -> "s":  # noqa: N802,F821
        raise DBusError(REJECTED, "PIN codes are not supported")

    @method()
    def RequestPasskey(self, device: "o") -> "u":  # noqa: N802,F821
        raise DBusError(REJECTED, "Passkeys are not supported")

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):  # noqa: N802,F821
        raise DBusError(REJECTED, "PIN codes are not supported")

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):  # noqa: N802,F821
        pass

    @method()
    def Cancel(self):  # noqa: N802
        _LOGGER.info("BlueZ cancelled the pairing request")
