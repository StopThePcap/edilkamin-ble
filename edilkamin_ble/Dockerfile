ARG BUILD_FROM
FROM $BUILD_FROM

# Install system dependencies. The add-on itself talks to the host's
# bluetoothd over D-Bus (dbus-fast) and to the kernel directly. `bluez` is only
# here because bleak runs `bluetoothctl --version` at startup to pick feature
# flags, and logs a warning on every start without it.
RUN apk add --no-cache \
    python3 \
    py3-pip \
    bluez

# Install Python dependencies.
# Upper bounds are deliberate: without them a rebuild can silently pull a new
# major release and change BLE or MQTT behaviour under a running install.
RUN pip3 install --no-cache-dir --break-system-packages \
    "bleak>=1.1,<2.0" \
    "dbus-fast>=2.0,<6.0" \
    "paho-mqtt>=2.1,<3.0" \
    "pycryptodome>=3.20,<4.0"

# The base image ships tempio, a config-templating tool this add-on never runs.
# Its bundled Go libraries carry known vulnerabilities that image scanners
# report, so drop it rather than ship unused, vulnerable code.
RUN rm -f /usr/bin/tempio

# Copy application
COPY edilkamin_ble /app/edilkamin_ble

WORKDIR /app

# Run the application
CMD ["python3", "-m", "edilkamin_ble"]
