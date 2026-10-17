# ESP32 Motor Control — Car Security System

This sketch drives the simulated car hardware and acts as the trigger for face authentication. Walking up to the HC-SR04 distance sensor asks the Raspberry Pi to run face recognition; the Pi decides whether the face is authorized and replies; the ESP32 then moves the motors.

## Project Overview

The complete project uses face recognition for two separate actions:

- **First face scan:** Unlock the demo car door
- **Second face scan:** Start the engine

The sensor steps through these:

| Car state when someone walks up | ESP32 sends | On `AUTHORIZED` |
|---|---|---|
| Locked | `START_AUTH UNLOCK` | Unlock (stepper) |
| Unlocked, engine off | `START_AUTH IGNITION` | Engine on (DC motor) |
| Engine running | nothing (sensor ignored) | — |

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

## Presence Sensor

| Rule | Behaviour |
|---|---|
| Present | Closer than 20 cm for 3 readings in a row (one reading every 100 ms) |
| Gone | 30 cm or farther, or no echo, for 3 readings in a row (20–30 cm keeps the current state) |
| Trigger | Present and armed → `requestAuth()` |
| After an authorized unlock | If the person is still present 5 s later and the engine is off, `START_AUTH IGNITION` is sent automatically |
| After any other result | No new scan until the person leaves or 10 s pass, so someone standing there doesn't loop scans |
| Paused | While the stepper moves, a scan is pending, or the engine runs |

The Pi's `STOP`, `LOCK` and `UNLOCK` commands also start the 10 s wait, so a person still standing there isn't scanned straight away.

## LCD

| Line | Shows |
|---|---|
| 1 | `Ready`, `Scanning...`, or a result for 3 s: `Authorized`, `Unauthorized` (wrong face), `Denied` (blocked, no reply, or lockout), `Busy`, `Error`, `Car locked` |
| 2 | `LOCKED   IGN:OFF`, `LOCKED   IGN:ON`, `UNLOCKED IGN:OFF`, `UNLOCKED IGN:ON` |

The display only rewrites a line when its text changes.

## Serial Protocol

115200 baud, one command per line, newline-terminated, case-insensitive.

| Direction | Line | Meaning |
|---|---|---|
| ESP32 → Pi | `START_AUTH UNLOCK` / `START_AUTH IGNITION` | Person detected; run face recognition |
| Pi → ESP32 | `AUTH_PENDING` | Scan started; restarts the 30 s wait timer |
| Pi → ESP32 | `AUTHORIZED` | Face authorized; ESP32 unlocks or starts the engine |
| Pi → ESP32 | `UNAUTHORIZED` | A face was seen but didn't match an allowed user; nothing moves |
| Pi → ESP32 | `DENIED` | Refused without a match (no face, lockout, cooldown); nothing moves |
| Pi → ESP32 | `BUSY` | Pi is already running another scan |
| Pi → ESP32 | `ERROR` | Pi could not run the scan |

Authentication replies are ignored unless a scan request is waiting for one. If the Pi does not answer within 30 seconds the ESP32 gives up and shows `Denied`.

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
- [x] Button-triggered authentication (ESP32 side)
- [x] HC-SR04 presence trigger and 1602A LCD status
- [ ] Full system integration
