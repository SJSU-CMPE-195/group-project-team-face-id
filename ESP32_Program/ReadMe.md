
# ESP32 Motor Control — Component Test

This sketch is a hardware integration test. It tests the 2 DC motors to perform a lock sequence and engine ignition independently before integrating them with the face ID workflow.

## Project Overview

The complete project is designed to use face recognition for two separate actions:

- **First face scan:** Unlock the demo car door
- **Second face scan:** Start the engine

This sketch simulates both actions using two DC motors controlled via an L298N motor driver and an ESP32.

## Hardware

| Component | Role |
|---|---|
| ESP32 | Microcontroller |
| L298N Motor Driver | Controls both DC motors |
| DC Motor A (OUT1/OUT2) | Simulates engine ignition |
| DC Motor B (OUT3/OUT4) | Simulates lock/unlock mechanism |

## Wiring

| ESP32 Pin | L298N Pin |
|---|---|
| GPIO14 | ENA |
| GPIO26 | IN1 |
| GPIO27 | IN2 |
| GPIO15 | ENB |
| GPIO18 | IN3 |
| GPIO19 | IN4 |
| GND | GND |

| Input Terminal | L298N Pin |
|---|---|
| PWR SOURCE| +12V |
| DC Motor A T1 | OUT 1|
| DC Motor A T2 | OUT 2|
| DC Motor B T1 | OUT 3|
| DC Motor B T2 | OUT 4|

ESP32 powered via USB.

## Serial Monitor Commands

Open Serial Monitor at **115200 baud** with line ending set to **Newline**.

| Command | Action |
|---|---|
| `on` | Turn engine motor on |
| `off` | Turn engine motor off |
| `flip` | Reverse engine motor direction |
| `speed X` | Set engine motor speed (0–255) |
| `lock` | Run lock motor to locked position |
| `unlock` | Run lock motor to unlocked position |

## Status

- [x] DC motor on/off and direction control
- [x] Lock/unlock motor mechanism
- [x] Face ID integration
- [ ] Full system integration

## Notes

Lock and unlock timing can be tuned in the sketch:
```cpp
const int LOCK_SPEED = 200;  // 0-255
const int LOCK_TIME  = 500;  // milliseconds
```
