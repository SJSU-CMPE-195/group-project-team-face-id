# ESP32 Relay Control — 2003 Accord Face Access

This sketch replaces the motor bench firmware for the real car. Four relays
imitate the car's own switches: the driver's key cylinder disarms and arms the
alarm, and the passenger lock switch unlocks and locks all doors. The ESP32
talks to the Raspberry Pi over the GPIO UART, not USB.

## Wiring

### Pi ↔ ESP32 (UART2, 115200 8N1)

| Pi 5 header | ESP32 |
|---|---|
| GPIO14 / TXD (pin 8) | GPIO16 (Serial2 RX) |
| GPIO15 / RXD (pin 10) | GPIO17 (Serial2 TX) |
| GND (pin 6) | GND |

Both sides are 3.3 V. The GND wire is required because the ESP32 runs from the
external PSU, not from the Pi.

### Relays

| ESP32 | Relay | Car wire on **NO** | **COM** to |
|---|---|---|---|
| GPIO26 | DISARM | Driver white (gray plug) | Driver black |
| GPIO27 | UNLOCK | Passenger pink/blue | Passenger black |
| GPIO32 | LOCK | Passenger pink/black | Passenger black |
| GPIO33 | ARM | Driver white/red (gray plug) | Driver black |
| 5V / GND | DC+ / DC− on all relays | — | — |

Trigger jumpers on **H**. 10 kΩ pull-down from each IN to DC−. **NC stays
empty on every relay.**

### LCD and HC-SR04 (moved for the relays and UART)

| ESP32 | Connection | Change from the motor firmware |
|---|---|---|
| GPIO15 | LCD RS | — |
| GPIO23 | LCD E | — |
| GPIO13 | LCD D4 | — |
| GPIO25 | LCD D5 | — |
| GPIO18 | LCD D6 | Was GPIO17 (now UART2 TX) |
| GPIO4 | LCD D7 | — |
| GPIO21 | HC-SR04 TRIG | Was GPIO32 (now LOCK relay) |
| GPIO34 | HC-SR04 ECHO through 1k/2k divider | Was GPIO33 (now ARM relay) |

## Pi setup

1. `dtparam=uart0=on` in `/boot/firmware/config.txt`, then reboot. The header
   UART appears as `/dev/ttyAMA0`. Leave `serial0` (the debug connector,
   `ttyAMA10`) as the console.
2. The runtime uses `ESP32_SERIAL_PORT` when set, then a USB ESP32, then
   `/dev/ttyAMA0`. Set `ESP32_SERIAL_PORT=/dev/ttyAMA0` to be explicit.
3. Set `ESP32_IGNITION_ENABLED=0`. This car has no ignition relay, so the Pi
   refuses ignition requests instead of reporting an engine that never started.
4. Set `ESP32_LOCK_ENABLED=0`. Our system never locks or arms this car on its
   own: the Pi sends no `LOCK` on startup, ESP32 connect, shutdown or as a
   fail-safe (only `STOP`), never auto-relocks, and the host dashboard **Lock**
   returns 409 "Locking is disabled on this vehicle". The **only** way our
   system locks is the phone app's **Lock** button (Console tab, the user's
   PIN): the Pi then sends `STOP` + `SECURE`, which locks the doors and arms
   the alarm. The physical key or fob works as normal.
5. Run the wireless host in Pi mode (`bass_wireless.py --mode pi`); it starts
   the ESP32 link that listens for walk-up requests:

   ```bash
   export FACEID_DB_PATH=/home/bass/faceid/faceid.db
   export ESP32_SERIAL_PORT=/dev/ttyAMA0 ESP32_IGNITION_ENABLED=0 ESP32_LOCK_ENABLED=0
   .venv/bin/python bass_wireless.py --mode pi --port 5056 --dashboard-port 5057
   ```

## Protocol

The Pi side is unchanged: it is the request-ID protocol of the motor firmware
(see [../ReadMe.md](../ReadMe.md#serial-protocol)). The proposed overview
messages map onto it like this:

| Overview message | Line on the wire |
|---|---|
| `PRESENCE` | ESP32 → Pi `START_AUTH UNLOCK <request_id>` |
| `ENTRY` (face match) | Pi → ESP32 `AUTHORIZED <request_id>`; ESP32 replies `AUTH_ACK <request_id>` and runs ENTRY |
| `DENY` | Pi → ESP32 `UNAUTHORIZED` / `DENIED <request_id>` |
| `SECURE` | Pi → ESP32 `LOCK` |
| `STOP` | Pi → ESP32 `STOP` (cancels the scan, all relays off) |
| `CLEAR` | ESP32 → Pi `Person gone` (informational) |
| `START <cmd>` / `DONE <cmd>` | ESP32 → Pi `START ENTRY`, `DONE SECURE`, `ABORT ENTRY` (informational) |

A grant only works for the request the ESP32 is currently waiting for. Each ID
is a random boot nonce plus a counter and is accepted once, so a recorded
`AUTHORIZED` line can't be replayed later. The UART is not authenticated: a
device spliced into the wire while a scan is pending could still forge a grant.
HMAC on `AUTHORIZED` and `LOCK` is still planned.

| Pi → ESP32 | Action |
|---|---|
| `UNLOCK` (alias `ENTRY`) | DISARM 1000 ms, gap 500 ms, UNLOCK 500 ms |
| `SECURE` | Phone Lock: LOCK 500 ms, gap 500 ms, ARM 1000 ms. Sent by the Pi only for a PIN-authorized phone Lock |
| `LOCK` | **Ignored** while `ALLOW_LOCK = false` (default): replies `Locking disabled; LOCK ignored`. With `ALLOW_LOCK = true` it runs the same sequence as `SECURE` |
| `STOP` | Cancel a pending scan and turn every relay off |
| `START` | Ignored: no ignition relay |
| `STATUS` | Replies `STATUS <LOCKED/UNLOCKED> SEQ:<name> AUTH:<IDLE/WAITING>` |

Sequences don't block: the sensor, LCD and serial keep running. The newest
command replaces a running sequence instead of replying `BUSY`. The Pi sends
`STOP` then `LOCK` back to back, and a compensating `LOCK` straight after a
failed unlock, and neither may be dropped.

With `ESP32_LOCK_ENABLED=0` the Pi sends only `STOP` when it (re)connects
and on shutdown, and logs `lock skipped`. Starting or stopping the Pi service
does not change the car. `ALLOW_LOCK = false` in the sketch is a second guard:
even a `LOCK` from a misconfigured Pi does nothing. Only `SECURE` locks and
arms, and the Pi sends it only for a phone Lock.

## Behaviour

| Event | Result |
|---|---|
| Person ≤ 20 cm for 3 readings | `START_AUTH UNLOCK`, LCD `Scanning...` |
| Authorized | ENTRY, LCD `Welcome`; no new scan until the person walks away |
| Not authorized / no reply in 30 s | LCD `Access denied` / `Denied`; rescan after they leave or 10 s |

Every walk-up asks for an unlock because the car gives no lock feedback.
Disarming and unlocking an already-open car is harmless. Line 2 of the LCD
shows the last completed sequence (`Doors: LOCKED` / `Doors: UNLOCKED`). The
ESP32 saves it in flash, but it is what the ESP32 last told the car, not a
measured state.

## USB Serial Monitor (bench testing)

115200 baud, Newline. Every line sent to the Pi is also echoed here. Typed
commands: `lock`, `unlock`, `stop`, `status`, plus single pulses `disarm` and
`arm` (USB only) for testing the ARM relay; `lock` and `arm` are ignored while
`ALLOW_LOCK = false`. Type `secure` to test the phone-lock sequence (LOCK then
ARM relay). Authentication replies are only
accepted from the Pi link. Set `ALLOW_USB_COMMANDS = false` for the showcase.

Use only one power source at a time: unplug the PSU from the ESP32's 5V pin
while it is on USB, or use a data-only cable.

## Build

These commands are used when the ESP32 is connected and communicating to your Raspberry Pi 5 through USB to flash the .ion file onto the ESP32, NOT when ESP32 and PI are communicating UART via TX and RX GPIO Pins.

```bash
arduino-cli compile --profile accord ESP32_Program/Accord_Face_Access
arduino-cli upload --profile accord -p /dev/ttyUSB0 ESP32_Program/Accord_Face_Access
```

This sketch has its own folder because Arduino builds every `.ino` in a folder
together; next to `ESP32_Program.ino` both sketches would fail to compile.
