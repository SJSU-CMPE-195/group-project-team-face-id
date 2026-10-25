# Hardware reference

[Prototype setup](../README.md#installation) · [Firmware guide](../ESP32_Program/ReadMe.md)

Bench wiring, planned CAN work and troubleshooting. USB serial is the current
transport; CAN is not implemented. Run commands from the repository root.

## Hardware wiring

The [current firmware guide](../ESP32_Program/ReadMe.md) is the source for the
complete pin map, HC-SR04 trigger, LCD wiring and request-ID protocol. The tables
below summarize the current serial bench setup; CAN parts are future work.

### Bill of materials

| Component | Role | Notes |
|---|---|---|
| Raspberry Pi 5 | Host: camera, recognition, API, dashboard | Runs `bass_wireless.py` |
| Pi Camera Module (CSI) | Enrollment and unlock capture | Pi 5 uses a **22-pin 0.5 mm** cable, not the 15-pin Pi 4 type |
| ESP32 (WROOM, CP2102N USB) | Motor controller | Appears as `/dev/ttyUSB0` |
| L298N dual H-bridge | DC motor driver | Engine ignition motor |
| DC motor | Simulates engine ignition | Driven from L298N OUT1/OUT2 |
| ULN2003 driver board | Stepper driver | Normally ships with the 28BYJ-48 |
| 28BYJ-48 stepper | Simulates the door lock actuator | 2048 half-steps per lock throw |
| HC-SR04 + 1k/2k resistors | Presence trigger | Divide the 5 V ECHO signal before GPIO33 |
| 1602A LCD + 10k potentiometer | Controller status display | Parallel 4-bit interface |
| MCP2515 CAN module (planned) | Future CAN controller for the Pi | Not required for the current serial prototype |
| SN65HVD230 (planned) | Future CAN transceiver for the ESP32 | Pin allocation must be revised before connecting |
| External 12 V supply | Motor power | Never power motors from the ESP32 |

### ESP32 GPIO allocation

| GPIO | Signal | Connects to |
|---|---|---|
| 14 | `ENA` — PWM, 1 kHz, 8-bit | L298N `ENA` |
| 26 | `IN1` | L298N `IN1` |
| 27 | `IN2` | L298N `IN2` |
| 18 | `STEP_IN1` | ULN2003 `IN1` |
| 16 | `STEP_IN2` | ULN2003 `IN2` |
| 21 | `STEP_IN3` | ULN2003 `IN3` |
| 22 | `STEP_IN4` | ULN2003 `IN4` |
| 32 | `TRIG_PIN` | HC-SR04 TRIG |
| 33 | `ECHO_PIN` | HC-SR04 ECHO through 1k/2k divider |
| 15, 23 | `LCD_RS`, `LCD_E` | LCD RS and E |
| 13, 25, 17, 4 | `LCD_D4` through `LCD_D7` | LCD data lines |

GPIO4 is used by the current LCD. The historical CAN RX proposal below conflicts
with it; choose and validate another pin before attempting CAN integration.

### DC motor — engine ignition (L298N)

| ESP32 | L298N |
|---|---|
| GPIO14 | `ENA` |
| GPIO26 | `IN1` |
| GPIO27 | `IN2` |
| GND | `GND` (must be common with the supply) |

| L298N terminal | Connects to |
|---|---|
| `+12V` | External 12 V supply (+) |
| `GND` | Supply (−) **and** ESP32 GND |
| `OUT1` | DC motor terminal 1 |
| `OUT2` | DC motor terminal 2 |

Speed is PWM on `ENA` through `ledcAttach(ENA, 1000, 8)`, so `SPEED 0`–`SPEED 255`
maps directly onto duty cycle. Direction comes from IN1/IN2: `HIGH`/`LOW` runs the
motor, `LOW`/`LOW` lets it coast to a stop.

### Stepper motor — door lock (ULN2003 + 28BYJ-48)

| ESP32 | ULN2003 |
|---|---|
| GPIO18 | `IN1` |
| GPIO16 | `IN2` |
| GPIO21 | `IN3` |
| GPIO22 | `IN4` |
| GND | `GND` (common) |

| ULN2003 | Connects to |
|---|---|
| `+5V` | 5 V supply — the L298N's onboard 5 V regulator can provide this |
| `GND` | Common ground |
| 5-pin socket | 28BYJ-48 keyed connector |

The firmware constructs the stepper like this:

```cpp
AccelStepper lockMotor(AccelStepper::HALF4WIRE, STEP_IN1, STEP_IN3, STEP_IN2, STEP_IN4);
```

`STEP_IN2` and `STEP_IN3` are deliberately swapped in that call. This is the
correct coil ordering for a 28BYJ-48 driven through a ULN2003 — do not "correct"
it to sequential order, or the motor will buzz and vibrate without rotating. One
lock throw is `LOCK_STEPS = 2048` half-steps.

### CAN bus — Raspberry Pi side (MCP2515, SPI)

> **Voltage warning — read before powering on.** Most MCP2515 breakout boards (the
> ones carrying a TJA1050 transceiver) are designed to run at 5 V. At 5 V the
> module's `SO`/MISO pin drives roughly 5 V into the Pi's GPIO 9, which is
> **3.3 V only and not 5 V tolerant**. This can damage the SoC. Choose one of:
> power the module from 3.3 V (Pin 1) instead — the MCP2515 itself runs fine at
> 3.3 V, and on a short two-node bench bus against a 3.3 V SN65HVD230 this
> generally works; or level-shift the MISO line alone; or use a 3.3 V-native
> module.

| MCP2515 pin | Pi physical pin | Pi signal |
|---|---|---|
| `VCC` | 1 (preferred) or 2 | 3.3 V — or 5 V, with the warning above |
| `GND` | 6 | GND |
| `CS` | 24 | GPIO8 / SPI0 CE0 |
| `SO` | 21 | GPIO9 / SPI0 MISO |
| `SI` | 19 | GPIO10 / SPI0 MOSI |
| `SCK` | 23 | GPIO11 / SPI0 SCLK |
| `INT` | 22 | GPIO25 |

`INT` on GPIO25 must match the `interrupt=` value in the device-tree overlay below.

### CAN bus — ESP32 side (SN65HVD230)

Planned wiring only. The proposed GPIO4 RX assignment below is unavailable in
the current LCD firmware and must be redesigned before connecting this module.

| SN65HVD230 pin | ESP32 |
|---|---|
| `VCC` / `3V3` | 3V3 — **not** 5 V |
| `GND` | GND (common) |
| `CTX` / `D` | GPIO5 (TWAI TX) |
| `CRX` / `R` | GPIO4 (TWAI RX) |

### CAN bus — between the two nodes

| MCP2515 (Pi) | SN65HVD230 (ESP32) |
|---|---|
| `CANH` | `CANH` |
| `CANL` | `CANL` |

Both ends of a CAN bus need a 120 Ω termination resistor — two in total, no more
and no fewer. Both modules usually carry one on board, enabled by a jumper or
solder bridge; with exactly two nodes, enable both. Verify with a multimeter:
measure across `CANH`/`CANL` with everything powered **off** and expect roughly
**60 Ω** (the two 120 Ω resistors in parallel).

### Power and ground rules

- **One common ground.** ESP32 GND, L298N GND, ULN2003 GND and the 12 V supply
  negative must all be tied together, or control signals have no reference and
  behaviour becomes erratic.
- **Never drive motors from the ESP32's 3V3 or 5V pins.** Motor current must come
  from the external supply through the driver boards.
- The ESP32 itself is powered over USB from the Pi during testing.
- Bring the 12 V supply up only after the signal wiring has been checked.

## CAN bus migration (planned, not yet implemented)

**Status: design only.** No CAN code exists in this repository yet — the Pi still
talks to the ESP32 over USB serial at 115200 baud. This section records the
intended change so the wiring above has context.

### Command delivery and physical feedback

The current Pi prototype sends motor commands over serial after authorization.
It can report a successful write, but cannot confirm that the motor moved or
that the lock reached its requested position. CAN remains a planned transport
change, not a prerequisite for the existing serial controls.

The old GPIO4 RX proposal also conflicts with the current LCD. CAN requires a
new pin allocation as well as protocol and runtime work.

Transport acknowledgements alone would not establish physical state. Any future
status frames must distinguish command receipt from measured lock and ignition
state; physical confirmation still requires hardware feedback and validation.

### Proposed frame format

| ID | Direction | Payload |
|---|---|---|
| `0x100` | Pi → ESP32 | `[cmd, arg, counter]` — cmd: 1=LOCK, 2=UNLOCK, 3=START, 4=STOP, 5=SPEED (`arg` 0–255) |
| `0x101` | ESP32 → Pi | `[locked, engine_on, speed, last_cmd_echo]` |

Fixed 8-byte frames remove the string parsing and partial-read handling that the
current `Serial.readStringUntil('\n')` loop needs.

### Raspberry Pi OS configuration

Add to `/boot/firmware/config.txt`:

```
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
```

Set `oscillator=` to match the crystal can soldered to your MCP2515 board —
**8 MHz and 16 MHz are both common**. A mismatch brings the interface up cleanly
and then passes zero frames, which is the single most common cause of a CAN bus
that appears dead for no visible reason.

After a reboot:

```bash
sudo ip link set can0 up type can bitrate 500000
candump can0      # from can-utils
```

Confirm frames move with `candump` **before** writing any Python. Use the same
bitrate on both nodes; 500 kbit/s is the automotive norm.

### Code that will change

| Area | File |
|---|---|
| Add `python-can` | `requirements-pi-device-api.txt` |
| Replace the serial transport with a CAN bus handle | `car_face_auth/src/pi_runtime.py` |
| Replace the serial `_send_command` implementation with CAN send + ACK handling | `car_face_auth/src/pi_runtime.py` |
| Remove USB-descriptor port discovery (`ESP_KEYWORDS`, `_find_esp_port`) | `car_face_auth/src/pi_runtime.py` |
| Add a receive path for `0x101` status frames | `car_face_auth/src/pi_runtime.py` |
| Rename `esp32_connected` / `serial_port` status fields to CAN equivalents | `car_face_auth/src/pi_runtime.py`, dashboard status consumers |
| Provide a CAN twin of `_SimulatedSerial` | `car_face_auth/src/simulated_runtime.py` |
| Port the command loop to the TWAI driver (`driver/twai.h`) and emit status frames | `ESP32_Program/ESP32_Program.ino` |
| Update serial mocks | `tests/test_runtime_safety.py`, `tests/test_simulated_pi_api.py` |

Keeping the `_send_command(command: str) -> bool` signature and swapping only its
body means the scan, unlock and auto-relock logic above it needs no changes.

### Security note

CAN has no authentication. Any node physically on the bus can inject an unlock
frame and the ESP32 will obey. That is not a defect in this design — it is how
real vehicle CAN buses behave, and it underpins most published car-hacking work.
Because the goal here is a realistic vehicle environment, this prototype
reproduces that property faithfully. The authentication, session and
authorization controls in the [host guide](android-wireless.md#api-and-recovery) protect the **network API**;
it does not extend to the CAN bus. A rolling counter plus a short MAC on command
frames would mitigate it if the project chooses to go further.

---

## Troubleshooting

### `rpicam-hello --list-cameras` reports "No cameras available!"

A lit LED on the camera board only proves the 3.3 V and GND pins are making
contact. Sensor detection happens over a separate I2C pair on the same ribbon.
Confirm what the kernel actually sees:

```bash
v4l2-ctl --list-devices          # expect an rp1-cfe entry, not only pispbe
dmesg | grep -iE "rp1-cfe|imx|ov5"
ls /dev/i2c-*                    # a camera creates its own I2C bus
```

If there is no `rp1-cfe` entry and no camera I2C bus, no overlay was loaded.
`camera_auto_detect=1` recognises official Raspberry Pi modules only and fails
silently on third-party boards. Force the sensor explicitly in
`/boot/firmware/config.txt` and reboot:

```
camera_auto_detect=0
dtoverlay=ov5647,cam0
```

Substitute `imx219`, `imx477` or `imx708` as appropriate, and `cam1` for the other
connector. If the sensor then probes, auto-detect simply did not know the board.
If `dmesg` instead reports `failed to read chip id`, the software is now correct
and the fault is physical — reseat the ribbon, confirm you are using a **22-pin
0.5 mm** Pi 5 cable rather than a 15-pin Pi 4 cable, and try the other connector.

CSI cameras are enumerated only at boot, so a camera connected to a running Pi
will never appear until it is rebooted.

### Enrollment and unlock scans return 503

`liveness_detection` defaults to `true`, but presentation-attack detection is not
implemented (`liveness_available = False`). `car_face_auth/src/runtime_safety.py`
fails closed in that combination. An administrator can disable the setting under
**Settings** for prototype testing; doing so reduces security and does not
establish production or anti-spoofing safety.

### A user exists but has no face template

Creating an account and capturing a face are separate steps. In **Users**, select
the existing account's face-enrollment action. An ordinary user can enroll only
their own account; administrators retain their permitted account-management
scope. Authorize with the signed-in user's fresh PIN. The host camera captures
the face. Cancellation or failure retains the account and any previously
enrolled template. Re-enrolling a blocked account does not restore access.

### "Start face unlock" is greyed out

Use the dashboard on the host itself: phones expose status and management, while
vehicle controls require the trusted local dashboard. Check the connection,
camera availability, active scan and displayed runtime errors. Pi prototype
mode supports serial controls; liveness requirements still apply.

### `STOP, LOCK command was not sent` at startup

The runtime could not send one or both startup safety commands. Check the ESP32
connection, serial device permissions and configured `ESP32_SERIAL_PORT`, then
read the accompanying serial error. A successful retry confirms command delivery
only; inspect the hardware to confirm its state.

### The dashboard shows stale UI

The host serves the built `dist/` directory. Run `npm run build` after frontend
changes, or use `npm run dev` on port 5173 during development.

### The host cannot find its database

`--mode pi` defaults to `/home/pi/faceid/faceid.db`. On a Pi whose user is not
`pi`, set the path explicitly:

```bash
export FACEID_DB_PATH=/home/<user>/faceid/faceid.db
```
