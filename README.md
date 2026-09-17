## Native Android wireless app

For the native APK, fixed QR pairing, and local Wi-Fi host setup, see
[Android wireless operation](docs/android-wireless.md). Every unlock requires
recognition by the selected backend's camera: PC webcam or Pi camera. Android
and the React dashboard control that same backend; neither UI chooses a
different camera for unlocking.

## Team members

| Name | SJSU Email | GitHub |
|------|------------|--------|
| Taanish Patel | [taanish.patel@sjsu.edu](mailto:taanish.patel@sjsu.edu) | [@pateltaanish](https://github.com/pateltaanish) |
| Adam Mejia | [adam.mejia@sjsu.edu](mailto:adam.mejia@sjsu.edu) | [@AdamMejia](https://github.com/AdamMejia) |
| Nick Thi | [nicholas.thi@sjsu.edu](mailto:nicholas.thi@sjsu.edu) | [@nicholastee22](https://github.com/nicholastee22) |
| Greg Lu | [greg.lu@sjsu.edu](mailto:greg.lu@sjsu.edu) | [@vvv017](https://github.com/vvv017) |

**Project advisor:** Eric Vanuska

---

## Project Description:
The Biometric Automobile Security System (B.A.S.S.) is a vehicle access control solution that replaces traditional keys with facial recognition for authentication. Using a Raspberry Pi and camera module, the system performs real-time identity verification against a local database and, upon successful recognition, enables door unlocking and ignition through connected hardware. This approach provides a secure, contactless, and user-centric alternative to conventional key-based vehicle security.

This repository contains two main pieces: a **React dashboard** for controlling and monitoring the system (`src/`), and a **Python facial-recognition PoC** that runs on a Raspberry Pi (or a dev PC) inside `car_face_auth/`.

## Repository layout

```
group-project-team-face-id/
├── src/                          # React dashboard
│   ├── components/               # UI components
│   ├── hooks/                    # useAppState, useAppActions, useApi
│   ├── utils/                    # Helper functions
│   ├── App.jsx
│   └── main.jsx
├── car_face_auth/
│   ├── src/
│   │   ├── api_server.py         # FastAPI — browser frames → InsightFace
│   │   ├── face_engine.py        # Shared embeddings + inference (SQLite-backed)
│   │   ├── enroll.py             # CLI enrollment (Pi camera)
│   │   ├── verify_live.py        # CLI live verification (Pi camera + ESP32)
│   │   └── test_insightface.py   # Debug enrollment script
│   └── requirements.txt
├── db.py                         # SQLite schema + init
├── db_api.py                     # Database access layer
├── pi_device_api.py              # Flask REST API (runs on Pi)
├── requirements-pi-device-api.txt
├── systemd/                      # Auto-start service files for Pi
│   └── faceid-api.service         # API + camera + ESP32 owner
├── install.sh                    # One-shot Pi setup script
└── ESP32_Program/                # ESP32 firmware
```

---

## Frontend (React UI)

### Quick start

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

For PC webcam operation, also start `scripts/start-wireless.cmd`. The local
development dashboard connects to this authenticated host by default. Enroll a
face, then start Unlock from either the dashboard or the paired Android app.
The PC captures its webcam and unlocks only after a successful match. Its
actuator output remains simulated. See [host setup](docs/android-wireless.md).

Keep the host dashboard's Control tab open to see its camera view when Android
starts a scan. See [watch a phone-triggered scan](docs/android-wireless.md#watch-a-phone-triggered-scan-on-the-host).

### Developer-only hardware simulator

Normal PC operation uses its real webcam. The separate HTTP simulator below is
for scripted hardware/recognition development, not for verifying a real face.
It is not a camera or operating mode offered by the normal UI.

To test the real HTTP contract and production `PiRuntime` state machine without
Raspberry Pi / camera / ESP32 hardware, start the standalone simulator. It replaces
only the hardware and face-engine seams; scan windows, authorization, enrollment,
cancel, lock, and ignition behavior still run through the canonical Device API:

```bash
npm run mock:pi
# equivalent: python mock_pi_device_api.py
```

Use this developer fixture through its standalone API:

```text
http://localhost:5055
```

The server creates a local `Demo Driver` and stores its disposable SQLite data under
`.cache/`. Configure a successful camera stream from another terminal:

```bash
curl -X PUT http://localhost:5055/sim/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario":{"frames":[{"identity":"Demo Driver","face_count":1,"score":0.91}],"frame_delay_ms":75,"camera_error":null,"camera_stalled":false},"serial_connected":true,"fail_commands":[]}'
```

The last scripted frame repeats until the session finishes. This lets the real
rolling-window logic reach its required 6 matches out of 10 observations. The
developer-only simulator controls are:

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/sim/scenario` | Inspect frames, faults, readiness, and command history |
| PUT / POST | `/sim/scenario` | Set scripted frames and failure conditions |
| GET | `/sim/commands` | Read attempted `LOCK`, `UNLOCK`, `START`, and `STOP` commands |
| POST | `/sim/reset` | Cancel active work, attempt `STOP` + `LOCK`, and restore the camera scenario |

Useful fault fields are `camera_error`, `camera_stalled`, `serial_connected`, and
`fail_commands` (any of `LOCK`, `UNLOCK`, `START`, `STOP`). A frame with
`face_count: 0` simulates no face; `face_count: 2` simulates multiple faces; an
unknown `identity` simulates a non-match. For example, this makes the ESP32 reject
unlock while leaving the database locked:

```bash
curl -X PUT http://localhost:5055/sim/scenario \
  -H "Content-Type: application/json" \
  -d '{"frames":[{"identity":"Demo Driver"}],"fail_commands":["UNLOCK"]}'
```

The HTTP simulator exercises the Python API and runtime using its scripted
frames. The normal dashboard uses an HTTP backend and does not offer the old
in-browser fake as an operating mode.

This simulator cannot certify Picamera2 compatibility or frame rate, InsightFace
performance on the Pi, USB serial permissions, real ESP32 acknowledgements, motor
direction/limits/electrical safety, or systemd startup with attached hardware.

### Android installation and the host dashboard

Use the native APK for phone operation: see [Android wireless operation](docs/android-wireless.md).
Scan the host's fixed QR once; Android discovers and authenticates that host on
the local network. No USB forwarding, browser backend address, or separate
Connect action is needed for normal use.

The built web dashboard runs on the host at `http://localhost:5056`. Its PWA
manifest, icons, and offline shell remain available, but live controls and the
pairing QR always require the host connection. A standalone Vite preview or a
remote static website does not provide the trusted local API bridge.

### Standalone Face API (enrollment development)

The optional standalone Face API receives uploaded enrollment frames. It is not
the unlock service. Normal PC and Pi operation use the Device API and the
backend's own camera; they need no separate Face API process.

1. **Terminal A — Face API** (from repo root):

   ```bash
   cd car_face_auth
   python -m venv venv
   ```

   Activate the venv (Windows: `venv\Scripts\activate`; macOS/Linux: `source venv/bin/activate`), then:

   ```bash
   pip install -r requirements.txt
   python -m uvicorn src.api_server:app --host 127.0.0.1 --port 8765
   ```

   First run may download InsightFace **buffalo_s** weights into `~/.insightface/models/`.

2. **Terminal B — UI**:

   ```bash
   npm run dev
   ```

Configure `FACEID_DB_PATH` consistently when using this development service.
Its `/api/verify-frame` response is diagnostic recognition data and does not
authorize an unlock. The dashboard's Unlock action always asks the selected
backend to capture and verify its own camera.

### Components reference

| Component | Location | Purpose |
|-----------|----------|---------|
| **Badge** | `src/components/Badge.jsx` | Small inline label |
| **Card** | `src/components/Card.jsx` | Rounded card container |
| **Btn** | `src/components/Btn.jsx` | Primary / secondary / danger / blue variants |
| **Input** | `src/components/Input.jsx` | Text input |
| **Switch** | `src/components/Switch.jsx` | Boolean toggle |
| **TabBtn** | `src/components/TabBtn.jsx` | Legacy tab styling helper |
| **Toast** | `src/components/Toast.jsx` | Notifications |
| **SidebarNav** | `src/components/SidebarNav.jsx` | Main nav (Control, Users, Logs, Settings) |
| **TopBar** | `src/components/TopBar.jsx` | Title strip, refresh, status |
| **Overview** | `src/components/Overview.jsx` | Backend / device / safety summary |
| **StatusPanel** | `src/components/StatusPanel.jsx` | Lock state, battery, signal, lock action |
| **DevicePairingCard** | `src/components/DevicePairingCard.jsx` | The current host's fixed QR for Android pairing |
| **Tabs** | `src/components/Tabs.jsx` | Tab strip helper where used |
| **ControlTab** | `src/components/ControlTab.jsx` | Backend-camera unlock and same-person ignition |
| **UsersTab** | `src/components/UsersTab.jsx` | Backend user list and face enrollment |
| **LogsTab** | `src/components/LogsTab.jsx` | Event log |
| **SettingsTab** | `src/components/SettingsTab.jsx` | Settings + save |

### Hooks reference

| Hook | Location | Purpose |
|------|----------|---------|
| **useAppState** | `src/hooks/useAppState.js` | Mode, sim, device cache, `faceApiUrl`, settings |
| **useAppActions** | `src/hooks/useAppActions.js` | Refresh, unlock, add/delete user, save settings |
| **useApi** | `src/hooks/useApi.js` | Device HTTP API vs simulation |

### Utils reference

| Utility | Location | Purpose |
|---------|----------|---------|
| **clamp** | `src/utils/helpers.js` | Clamp a number between min and max |
| **genId** | `src/utils/helpers.js` | Unique IDs for toasts, users, logs |
| **fmt** | `src/utils/helpers.js` | Format timestamp to locale string |

### Frontend features

- **One backend**: all normal controls use the selected Device API.
- **Backend camera**: PC webcam or Pi camera, selected by the running host.
- **Persistent UI state**: saved backend settings; previous simulation data is preserved.
- **Layout**: sidebar + main content, theme via `ThemeProvider` (`src/context/`).

---

## Prerequisites

### Mac
- Node.js LTS — download from [nodejs.org](https://nodejs.org)
- Python 3.11 (recommended — newer versions may not have `onnxruntime` wheels)
- Git

### Windows
- Node.js LTS — download from [nodejs.org](https://nodejs.org)
- Python 3.11 — download from [python.org](https://www.python.org/downloads/)
- Git — download from [git-scm.com](https://git-scm.com)

---

## Setup — Raspberry Pi (one time)

```bash
# Clone the repo and switch to the working branch
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
git checkout wired-main

# Run the install script — handles everything automatically
bash install.sh
```

The install script will:
- Create the SQLite database under the service user's home (`~/faceid/faceid.db`)
- Install Raspberry Pi OS's `python3-picamera2` package and all pip dependencies into `.venv`
- Add your user to the `dialout`, `video`, and `gpio` groups
- Install and enable the single `faceid-api.service` so the API owns the camera and ESP32
- Disable and remove the legacy separate verification service if it is present

By default the service account is the user that invoked `sudo`; override it
with `FACEID_SERVICE_USER=<user>` and the database directory with
`FACEID_DB_DIR=<path>`.

Runtime overrides such as `ESP32_SERIAL_PORT`, scan timeouts, and the enrollment
sample interval can be placed in `/etc/default/faceid` as `KEY=value` lines,
then applied with `sudo systemctl restart faceid-api`.
Use an unquoted numeric value for `PORT` (for example `PORT=5001`). Configure
the service database with `FACEID_DB_DIR` when running `install.sh`; overriding
`FACEID_DB_PATH` in `/etc/default/faceid` is not supported by the installer.

To verify services are running after install:
```bash
sudo systemctl status faceid-api
curl --fail http://127.0.0.1:5000/health
```

---

## Setup — Dashboard (Mac / Windows / Linux)

The React UI only needs Node.js. Run this from your laptop on the same WiFi network as the Pi.

### Mac / Linux

```bash
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
git checkout wired-main
npm ci
npm run dev
```

### Windows

```cmd
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
git checkout wired-main
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Running the full system

Use the [wireless host setup](docs/android-wireless.md) on the PC or Pi:

1. Build the dashboard and start the wireless backend. Pi deployment must
   include the built `dist/` directory; it is not included by Git.
2. Open [http://localhost:5056](http://localhost:5056) on the backend machine.
   The dashboard automatically uses that service and its camera.
3. In **Settings → Device pairing**, select **Show QR** and scan it with BASS
   Android on the same network. The phone and dashboard share one backend.
4. Select **Refresh** to load new phone enrollments in **Users**.

No backend address or Connect action is needed. For local frontend development,
`http://localhost:5173` uses the same host through Vite. A custom backend port
works with the built dashboard URL printed by the backend at startup.

---

## Enrolling a user

1. Go to the **Users** tab
2. Select the backend/device camera as the enrollment source
3. Enter a display name and click **Add & enroll face**
4. Wait for the backend to capture the required samples

The face embedding is stored in the selected backend's SQLite database and is available to
the Device API's scan flow.

---

## Face verification (dashboard)

1. Go to the **Control** tab
2. Start the Unlock face scan
3. Look at the backend's camera: PC webcam or Pi camera. Access requires the
   configured rolling-window match threshold (6 of 10 observations by default).
4. Pi sends `UNLOCK` to the ESP32; PC records simulated actuation. A PIN alone
   or uploaded browser/phone verification frames cannot unlock the device.

---

## Backend connection

The canonical store is **SQLite on the selected backend** (`db.py` / `db_api.py`).
React and Android read that host's users, settings, and state through the Device
API (`/api/status`, `/api/users`, ...).

### On the Pi

The supported setup is `bash install.sh` from the repository root. It creates
`.venv`, installs Picamera2 from Raspberry Pi OS, installs the pip requirements,
and enables `faceid-api.service`. For a manual start, use the same interpreter:

   ```bash
   FACEID_DB_PATH=/home/pi/faceid/faceid.db .venv/bin/python db.py
   FACEID_DB_PATH=/home/pi/faceid/faceid.db PORT=5000 .venv/bin/python pi_device_api.py
   ```

   Optional environment variables:

   - `FACEID_DB_PATH` — SQLite file path (default: `/home/pi/faceid/faceid.db`)
   - `PORT` — HTTP port (default: `5000`)
   - `ESP32_SERIAL_PORT` — explicit ESP32 port when USB metadata is not recognizable
   - `PI_SCAN_TIMEOUT_SECONDS` / `PI_ENROLL_TIMEOUT_SECONDS` — camera session timeouts
   - `PI_ENROLL_SAMPLE_INTERVAL_SECONDS` — delay between accepted enrollment samples (default `0.5`)
   - `PI_CAMERA_CLOSE_TIMEOUT_SECONDS` — maximum shutdown wait for a blocked capture (default `2`)

   When running through systemd, put these overrides in `/etc/default/faceid`
   as `KEY=value` lines and restart `faceid-api`. Keep the database path under
   installer control with `FACEID_DB_DIR`; use an unquoted integer for `PORT`.

`faceid-api.service` uses `.venv/bin/python` and runs the root
`pi_device_api.py`. Do not start `verify_live.py` as a second service: the API
is the single owner of the Pi camera and ESP32 serial connection.

### In the UI

Normal dashboard operation uses `faceid-wireless.service` and its local page at
`http://localhost:5056`; see the [wireless Pi handoff](docs/android-wireless.md#pi-handoff).
The standalone `faceid-api.service` above is a separate development entrypoint.
Do not run both camera-owning services together.

### Schema (see `db.py`)

- `users`, `auth_logs`, `settings`, `device_state`

---

## Face recognition backend (Python PoC)

Facial-recognition vehicle access using a Raspberry Pi and camera: real-time detection, embeddings, local identity checks, and confidence-based unlock decisions.

### Proof-of-concept scope

**Included**

- Real-time face detection  
- Face embedding generation  
- Identity verification against a local database  
- Confidence-based access with a rolling window  

**Not in scope yet**

- Multi-user robustness testing  
- Anti-spoofing (photo/video)  
- Full vehicle integration  

**Implemented**

- Physical door lock and ignition control via ESP32

### Prerequisites

- Python 3.8+ (3.11–3.12 recommended on Windows if prebuilt wheels are missing)
- Raspberry Pi (or PC for development)
- A camera attached to the backend: Pi Camera Module on Pi or a USB webcam on PC.
- Virtual environment (required on Pi, recommended on dev PC)

**Python dependencies** (`requirements-pi-device-api.txt` includes
`car_face_auth/requirements.txt`): `flask`, `pyserial`, `insightface`,
`onnxruntime`, `opencv-python`, `numpy`, `fastapi`, `uvicorn[standard]`, and
`python-multipart`. Picamera2 is installed by Raspberry Pi OS with
`sudo apt install python3-picamera2` and exposed to `.venv` via
`--system-site-packages`.

### Running the PoC

**Option A — CLI (OpenCV window)**

1. **Enroll** (from `car_face_auth/`):

   ```bash
   python src/enroll.py
   ```

   Enter a username when prompted. Press **`s`** in the **OpenCV window** to save each sample; you need **10** samples.

2. **Live verification**:

   ```bash
   python src/verify_live.py
   ```

   You should see face bounding boxes, similarity scores, and messages such as `ACCESS PENDING` and `ACCESS GRANTED`.

**Option B — Device API + React or Android**

See [Android wireless operation](docs/android-wireless.md) for the PC/Pi host,
pairing, and local dashboard setup. Both UIs ask the backend to capture the face
for unlock. Use the optional standalone Face API only for enrollment/recognition
development; its uploaded frames cannot authorize device unlock.

### Demo screenshots

**Enrollment**

![Enrollment demo](car_face_auth/images/demo2.png)

**Enrollment terminal output**

![Enrollment terminal](car_face_auth/images/demo1.5.png)

**Verify live**

![Verify live](car_face_auth/images/demo1.png)

---

## HTTP API routes (`pi_device_api.py`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/status` | Device status, lock state, battery, signal |
| POST | `/api/unlock` | Start a backend-camera unlock scan; returns a scan session |
| POST | `/api/lock` | Lock the device |
| POST | `/api/ignition/stop` | Stop ignition |
| POST | `/api/full-reset` | Stop ignition and lock the device |
| GET | `/api/users` | List all enrolled users |
| POST | `/api/users` | Add a new user |
| DELETE | `/api/users/<id>` | Remove a user |
| PATCH | `/api/users/<id>/access` | Enable/disable face access for a user |
| POST | `/api/verify-log` | Log a face verify event |
| POST | `/api/scan/start` | Start a backend-camera unlock or same-driver ignition scan |
| GET | `/api/scan/status?session_id=<id>` | Read scan progress/result |
| POST | `/api/scan/cancel` | Cancel a running backend-camera scan |
| POST | `/api/scan/sample` | Rejected: client images cannot verify unlock/ignition |
| POST | `/api/enroll/start` | Start backend-camera or client-camera enrollment |
| GET | `/api/enroll/status?session_id=<id>` | Read enrollment progress/result |
| POST | `/api/enroll/cancel` | Cancel a running enrollment |
| GET | `/api/logs` | Retrieve auth logs |
| GET | `/api/settings` | Get device settings |
| POST | `/api/settings` | Save device settings |
| GET | `/health` | Process liveness and runtime details |
| GET | `/ready` | Hardware readiness (`503` until camera/model/ESP32 are ready) |

`/health` proves that the HTTP process is alive and includes `runtime_ready` plus
camera/model/ESP32 details. It does not replace an on-Pi hardware acceptance test.
The standalone Pi API is an unauthenticated LAN prototype. The wireless host
wraps these routes with pairing-key authentication, and exposes minimal public
`/health` data. See [wireless API and recovery](docs/android-wireless.md#api-and-recovery)
for its contract. Neither entrypoint provides a direct manual unlock.

---

## Tech stack

| Area | Technology |
|------|------------|
| Dashboard | React + Vite + Tailwind CSS |
| Face recognition | InsightFace (buffalo_s model) |
| Face API | FastAPI + Uvicorn (optional dev-PC browser flow) |
| Device API | Flask |
| Database | SQLite via db_api.py |
| Camera (Pi) | Picamera2 |
| Hardware control | pyserial → ESP32 |
| Auto-start | systemd |

---

## Hardware integration (ESP32)

Firmware and setup notes live under **`ESP32_Program`** on the `wired-main` branch:

https://github.com/SJSU-CMPE-195/group-project-team-face-id/tree/wired-main/ESP32_Program

The Pi sends simple serial commands to the ESP32:

| Command | Action |
|---------|--------|
| `UNLOCK` | Unlock door |
| `LOCK` | Lock door |
| `START` | Start ignition |
| `STOP` | Stop ignition |
