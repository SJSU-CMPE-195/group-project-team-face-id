# ESP32 Motor Control — Car Security System

This sketch drives the simulated car hardware and acts as the trigger for face authentication. A button press asks the Raspberry Pi to run face recognition; the Pi decides whether the face is authorized and replies; the ESP32 then moves the motors.

## Project Overview

The complete project uses face recognition for two separate actions:

- **First face scan:** Unlock the demo car door
- **Second face scan:** Start the engine

One button steps through these:

| Car state when the button is pressed | ESP32 sends | On `AUTHORIZED` |
|---|---|---|
| Locked | `START_AUTH UNLOCK` | Unlock (stepper) |
| Unlocked, engine off | `START_AUTH IGNITION` | Engine on (DC motor) |
| Engine running | nothing (press ignored) | — |

## Hardware

| Component | Role |
|---|---|
| ESP32 | Microcontroller, powered and connected to the Pi over USB |
| L298N Motor Driver | Drives the engine DC motor |
| DC Motor A (OUT1/OUT2) | Simulates engine ignition |
| 28BYJ-48 Stepper + ULN2003 | Simulates lock/unlock mechanism |
| Momentary pushbutton | Face-auth trigger (stand-in for a door/start sensor) |
| LED + 220 Ω resistor (GPIO33) | Authentication status |

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
| GPIO32 | Pushbutton leg 1 (leg 2 to GND; internal pull-up, no resistor) |
| GPIO33 | 220 Ω resistor → LED long leg (+); LED short leg (−) to GND |
| GND | L298N GND, ULN2003 GND, pushbutton, LED (−) |

| Input Terminal | L298N Pin |
|---|---|
| PWR SOURCE| +12V |
| DC Motor A T1 | OUT 1|
| DC Motor A T2 | OUT 2|

ESP32 powered via USB from the Raspberry Pi.

## Status LED

| LED | Meaning |
|---|---|
| Solid on | Waiting for the Pi's answer |
| Fast blink (1.5 s) | Denied, busy, error, or no reply |
| Off | Idle |

## Serial Protocol

115200 baud, one command per line, newline-terminated, case-insensitive.

| Direction | Line | Meaning |
|---|---|---|
| ESP32 → Pi | `START_AUTH UNLOCK` / `START_AUTH IGNITION` | Button pressed; run face recognition |
| Pi → ESP32 | `AUTH_PENDING` | Scan started; restarts the 30 s wait timer |
| Pi → ESP32 | `AUTHORIZED` | Face authorized; ESP32 unlocks or starts the engine |
| Pi → ESP32 | `DENIED` | Not authorized; nothing moves |
| Pi → ESP32 | `BUSY` | Pi is already running another scan |
| Pi → ESP32 | `ERROR` | Pi could not run the scan |

Authentication replies are ignored unless a button press is waiting for one. If the Pi does not answer within 30 seconds the ESP32 gives up and returns to idle.

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
- [ ] Full system integration
