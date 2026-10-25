# ESP32 Motor Control — Car Security System

This sketch drives the simulated car hardware and acts as the trigger for face authentication. Walking up to the HC-SR04 distance sensor asks the Raspberry Pi to run face recognition; the Pi decides whether the face is authorized and replies; the ESP32 then moves the motors.

## Project Overview

The complete project uses face recognition for two separate actions:

- **First face scan:** Unlock the demo car door
- **Second face scan:** Start the engine

The sensor steps through these:

| Car state when someone walks up | ESP32 sends | On `AUTHORIZED <request_id>` |
|---|---|---|
| Locked | `START_AUTH UNLOCK <request_id>` | Unlock (stepper) |
| Unlocked, engine off | `START_AUTH IGNITION <request_id>` | Engine on (DC motor) |
| Engine running | nothing (sensor ignored) | — |

Any enrolled driver with face access may start the engine after a face-verified
unlock. The ignition scan verifies the driver who is currently present; it does
not require the same person who unlocked the car.

## Hardware

| Component | Role |
|---|---|
| ESP32 | Microcontroller, powered and connected to the Pi over USB |
| L298N Motor Driver | Drives the engine DC motor |
| DC Motor A (OUT1/OUT2) | Simulates engine ignition |
| 28BYJ-48 Stepper + ULN2003 | Simulates lock/unlock mechanism |
| HC-SR04 ultrasonic sensor | Face-auth trigger (person walks up) |
| 1602A 16×2 LCD + 10k pot | Status display |
| 1k + 2k resistors | Voltage divider for HC-SR04 ECHO (5 V → 3.3 V) |

## Wiring

| ESP32 Pin | Connection |
|---|---|
| GPIO14 | L298N ENA |
| GPIO26 | L298N IN1 |
| GPIO27 | L298N IN2 |
| GPIO18 | ULN2003 IN1 |
| GPIO16 | ULN2003 IN2 |
| GPIO21 | ULN2003 IN3 |
| GPIO22 | ULN2003 IN4 |
| GPIO32 | HC-SR04 TRIG |
| GPIO33 | HC-SR04 ECHO through divider (ECHO → 1k → GPIO33, GPIO33 → 2k → GND) |
| GPIO15 | LCD RS (moved from GPIO19, which was shorted to GND) |
| GPIO23 | LCD E |
| GPIO13 | LCD D4 |
| GPIO25 | LCD D5 |
| GPIO17 | LCD D6 |
| GPIO4 | LCD D7 |
| 5V / VIN | HC-SR04 VCC, LCD VDD and backlight (+) |
| GND | L298N GND, ULN2003 GND, HC-SR04 GND, LCD VSS, RW and backlight (−) |

LCD contrast (V0) goes to the wiper of a 10k pot between 5V and GND. If RS reads LOW at boot (shorted), the LCD is skipped and the car still works.

| Input Terminal | L298N Pin |
|---|---|
| PWR SOURCE| +12V |
| DC Motor A T1 | OUT 1|
| DC Motor A T2 | OUT 2|

ESP32 powered via USB from the Raspberry Pi.

## Build

Install [Arduino CLI 1.5.1](https://docs.arduino.cc/arduino-cli/installation/).
For a clean Linux x86-64 checkout, these commands install the verified CLI under
the ignored `.cache/` directory:

```bash
mkdir -p .cache/arduino-cli
curl -fsSL https://downloads.arduino.cc/arduino-cli/arduino-cli_1.5.1_Linux_64bit.tar.gz \
  -o .cache/arduino-cli/arduino-cli.tar.gz
echo "28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b  .cache/arduino-cli/arduino-cli.tar.gz" \
  | sha256sum -c -
tar -xzf .cache/arduino-cli/arduino-cli.tar.gz \
  -C .cache/arduino-cli arduino-cli
```

The official Windows x86-64 ZIP is
`https://downloads.arduino.cc/arduino-cli/arduino-cli_1.5.1_Windows_64bit.zip`
with SHA-256
`fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40`.
Arduino publishes the complete checksum list with the
[1.5.1 release](https://github.com/arduino/arduino-cli/releases/tag/v1.5.1).

Then compile from the repository root:

```bash
.cache/arduino-cli/arduino-cli compile --profile impl2 ESP32_Program
# Or, when Arduino CLI is on PATH:
arduino-cli compile --profile impl2 ESP32_Program
```

The `impl2` [build profile](https://docs.arduino.cc/arduino-cli/sketch-project-file)
pins the generic ESP32 board platform and both direct library dependencies.
Arduino CLI downloads missing profile resources into an isolated cache, so
installed global cores and libraries do not affect the build.
This command compiles the firmware only; it does not flash a board or prove that
the connected motors, sensor, LCD, serial link, or physical lock work.

## Presence Sensor

| Rule | Behaviour |
|---|---|
| Present | Closer than 20 cm for 3 readings in a row (one reading every 100 ms) |
| Gone | 30 cm or farther, or no echo, for 3 readings in a row (20–30 cm keeps the current state) |
| Trigger | Present and armed → `requestAuth()` |
| After an authorized unlock | If the person is still present 5 s later and the engine is off, `START_AUTH IGNITION <request_id>` is sent automatically |
| After any other result | No new scan until the person leaves or 10 s pass, so someone standing there doesn't loop scans |
| Paused | While the stepper moves, a scan is pending, or the engine runs |

The Pi's `STOP`, `LOCK` and `UNLOCK` commands cancel any pending authentication
and automatic ignition schedule. They also start the 10 s wait, so a person
still standing there is not scanned straight away.

## LCD

| Line | Shows |
|---|---|
| 1 | `Ready`, `Scanning...`, or a result for 3 s: `Authorized`, `Unauthorized` (wrong face), `Denied` (blocked, no reply, or lockout), `Busy`, `Error`, `Car locked` |
| 2 | `LOCKED   IGN:OFF`, `LOCKED   IGN:ON`, `UNLOCKED IGN:OFF`, `UNLOCKED IGN:ON` |

The display only rewrites a line when its text changes.

## Serial Protocol

115200 baud, one command per line and newline-terminated. Command names are
case-insensitive. The ESP32 generates each request ID as exactly 16 uppercase
hexadecimal characters: an 8-character random boot nonce followed by an
8-character counter. The Pi must echo that ID unchanged.

| Direction | Line | Meaning |
|---|---|---|
| ESP32 → Pi | `START_AUTH UNLOCK <request_id>` / `START_AUTH IGNITION <request_id>` | Person detected; run face recognition |
| Pi → ESP32 | `AUTH_PENDING <request_id>` | Scan started; restarts the 30 s wait timer |
| Pi → ESP32 | `AUTHORIZED <request_id>` | Face authorized; ESP32 accepts the requested action |
| Pi → ESP32 | `UNAUTHORIZED <request_id>` | A face was seen but did not match an allowed user; nothing moves |
| Pi → ESP32 | `DENIED <request_id>` | Refused without a match (no face, lockout, cooldown); nothing moves |
| Pi → ESP32 | `BUSY <request_id>` | Pi is already running another scan |
| Pi → ESP32 | `ERROR <request_id>` | Pi could not run the scan |
| ESP32 → Pi | `AUTH_ACK <request_id>` | Matching `AUTHORIZED` was accepted and the action was started |
| ESP32 → Pi | `AUTH_CANCEL <request_id>` | Request expired or was cancelled before authorization was accepted |

The firmware accepts a reply only while the matching request is waiting and has
not expired. Untagged replies, stale IDs and late authorization are ignored.
There is no legacy untagged fallback and no automatic retry of a grant.

If the Pi does not answer within 30 seconds, the ESP32 sends `AUTH_CANCEL`,
invalidates the request and shows `Denied`. This also happens when the Pi scan
timeout is configured longer than the controller timeout. `STOP`, `LOCK` and
`UNLOCK` cancel a pending request the same way.

`AUTH_ACK` confirms only that the controller accepted the command. It does not
confirm physical lock position or completed motor movement. The Pi and ESP32
firmware must be updated together because this protocol is not compatible with
the previous untagged messages. Raw `START`, `STOP`, `LOCK` and `UNLOCK`
commands remain supported.

## Serial Monitor Commands

Open Serial Monitor at **115200 baud** with line ending set to **Newline**.

| Command | Action |
|---|---|
| `on` / `start` | Turn engine motor on |
| `off` / `stop` | Turn engine motor off |
| `speed X` | Set engine motor speed (0–255) |
| `lock` | Move lock to the locked position (no-op if already locked) |
| `unlock` | Move lock to the unlocked position (no-op if already unlocked) |

The lock uses absolute stepper positions, so sending `lock` or `unlock` twice does not over-rotate. The last completed lock position is saved in the ESP32's flash (`Preferences`, namespace `carlock`) and restored at boot without moving, so a reset, a replug, or the Pi opening the port doesn't move the lock. On the very first boot after flashing nothing is saved yet and the sketch assumes unlocked, so place the stepper in the unlocked position (or type `unlock` once) before then. If power is cut mid-move, the ESP32 restores the last completed position.

## Status

- [x] DC motor on/off control
- [x] Lock/unlock motor mechanism
- [x] Face ID integration (Pi-driven commands)
- [x] HC-SR04 presence trigger and 1602A LCD status
- [ ] Full system integration
