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
The Biometric Automobile Security System (B.A.S.S.) is a vehicle access control
prototype that uses facial recognition for authentication. A Raspberry Pi or PC
camera performs identity verification against a local database. PC lock and
ignition behavior is simulated. Real Pi motor and ignition output is currently
blocked until command acknowledgement and physical-position feedback are
implemented.

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
│   │   ├── api_server.py         # Retired standalone API; import fails closed
│   │   ├── face_engine.py        # Shared embeddings + inference (SQLite-backed)
│   │   ├── enroll.py             # Retired standalone CLI stub
│   │   ├── verify_live.py        # Retired standalone CLI stub
│   │   └── test_insightface.py   # Historical experiment; not for deployment
│   └── requirements.txt
├── db.py                         # SQLite schema + init
├── db_api.py                     # Database access layer
├── pi_device_api.py              # Injectable Flask route factory; no listener
├── bass_wireless.py              # Canonical authenticated HTTPS host
├── requirements-pi-device-api.txt
├── systemd/                      # Auto-start service files for Pi
│   └── faceid-wireless.service    # HTTPS API + camera + ESP32 owner
├── scripts/install-wireless-pi.sh # Supported Pi installer
├── install.sh                    # Retirement stub for the old service
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

Sign in to the host dashboard as an administrator and keep its Control tab open
to see the camera view when Android starts a scan. Regular users can preview
only sessions started on their own device. See [watch a phone-triggered scan](docs/android-wireless.md#watch-a-phone-triggered-scan-on-the-host).

### Hardware Simulator tab on the current PC host

Build with `npm run build`, then start the existing launcher with
`scripts/start-wireless.cmd -HardwareSimulator`. Open the normal dashboard at
`http://localhost:5057` and select **Hardware** in its sidebar. Buttons appear
in the same dashboard before and after product login; other product tabs still
require sign-in.

Explicit PC development mode trusts direct localhost dashboard clients. A local
cookie and CSRF token are created and renewed in the background. The operator
uses no separate login, activation link, or browser window for these controls.

The panel uses the current PC database and phone connection. Hold its button
for three seconds to open pairing, or ten seconds to open recovery, then release.
The backend determines the elapsed time and active window. Simulated power off
closes windows and cancels work while preserving ownership.

Existing installations retain their accounts in `legacy` ownership state. To
exercise first use, choose **Developer reset**, review its scope, and type
`RESET`. The host drains work and creates a private coherent database/identity/key
backup before clearing product data. Repeating the same reset request returns
its original receipt. A failed reset keeps the product in maintenance.

After reset, scan the activation card with the updated Android app, open the
pairing window, and set the owner's name and PIN. Export recovery material to a
safe location outside the phone. A public device QR grants no access.

Ordinary startup and Pi mode have no developer control routes. The panel works
through the actual loopback dashboard with its own background session,
same-origin checks, and CSRF token. Do not enable development mode on a host
where local clients are untrusted. It does not implement a Wi-Fi access point
or GPIO.
See [operation and recovery](docs/android-wireless.md#hardware-simulator-and-developer-reset).

### Standalone scripted hardware fixture

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

The server binds only to loopback, rejects remote peers, creates a local
`Demo Driver`, and stores its disposable SQLite data under `.cache/`. Configure
a successful camera stream from another terminal on the same computer:

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
frames. It is an unauthenticated, loopback-only development fixture. The normal
dashboard and Android app do not route to it.

This simulator cannot certify Picamera2 compatibility or frame rate, InsightFace
performance on the Pi, USB serial permissions, real ESP32 acknowledgements, motor
direction/limits/electrical safety, or systemd startup with attached hardware.

### Android installation and the host dashboard

Use the native APK for phone operation: see [Android wireless operation](docs/android-wireless.md).
Scan the host's fixed QR once; Android discovers that host, verifies its pinned
TLS certificate, and authenticates over HTTPS on port 5056. No USB forwarding,
browser backend address, or separate Connect action is needed for normal use.

The selected Android release candidate is the unsigned BASS 0.4.0 (versionCode
5) APK. Current artifact hashes and verification results are recorded in the
[delivery snapshot](docs/android-delivery-status.md). It must be signed before
installation or distribution; signing and build details are in the Android
wireless guide.

The built web dashboard runs only on the host at `http://localhost:5057`. Its PWA
manifest, icons, and offline shell remain available, but live controls and the
pairing QR always require the host connection. A standalone Vite preview or a
remote static website does not provide the trusted local API bridge. During
frontend development, Vite on port 5173 proxies that loopback service.

First-owner activation uses the phone, its activation card, and a device pairing
window. The dashboard signs in with an existing host account; it cannot create
the initial owner. After the first successful sign-in with a name and PIN,
the browser remembers that account and needs only its PIN when the session
expires. Explicit sign-out clears the account selection. The browser keeps its
15-minute session in an HTTP-only cookie and asks for the signed-in user's PIN before each
sensitive operation; it does not store the PIN. Administrators manage users,
access, PIN resets, phone invites, paired devices, logs, and settings. Ordinary
users can operate only as themselves and cannot open administrator controls.

### Retired standalone Face API

The old FastAPI/Uvicorn enrollment service is retired. Importing
`car_face_auth.src.api_server` raises an error instead of creating a listener.
Do not start port 8765. Normal PC and Pi operation uses `bass_wireless.py`; the
selected backend captures enrollment and verification images through the
authenticated Device API.

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
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
```

On a development computer at the same source revision, run `npm ci` and
`npm run build`. Copy the complete generated `dist/` directory into this Pi
checkout alongside `bass_wireless.py`. Git does not include these build files,
and the Pi installer requires them before it can proceed.

Then run on the Pi:

```bash
bash scripts/install-wireless-pi.sh
```

The supported installer creates the database and persistent host identity,
installs the existing dependencies, and enables `faceid-wireless.service` as
the single camera/ESP32 owner. It also exports the pairing QR. See the
[Pi handoff](docs/android-wireless.md#pi-handoff) for build, identity-transfer,
OpenSSL, and verification details.

The root `install.sh` is a retirement stub, and the repository's
`faceid-api.service` unit is removed. The stub exits with an error and points to
the wireless installer without changing the system. During an upgrade, the
supported installer still detects enabled or active `faceid-api.service` and
`faceid-verify.service` units, reports them, and exits without changing them.
Review the installed legacy service before explicitly disabling it; the check
prevents two processes from competing for the camera or ESP32.

---

## Setup — Dashboard (Mac / Windows / Linux)

The React UI only needs Node.js. Run this from your laptop on the same WiFi network as the Pi.

### Mac / Linux

```bash
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
npm ci
npm run dev
```

### Windows

```cmd
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
npm ci
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Running the full system

Use the [wireless host setup](docs/android-wireless.md) on the PC or Pi:

1. Build the dashboard and start the wireless backend. Pi deployment must
   include the built `dist/` directory; it is not included by Git.
2. Open [http://localhost:5057](http://localhost:5057) on the backend machine.
   Sign in with an existing account, or complete phone-first activation using
   the Hardware Simulator on the development PC. The dashboard automatically
   uses that service and its camera.
3. As an administrator, choose **Users → Pair phone** for the intended user.
   The dashboard displays a five-minute invitation QR.
4. Scan that invitation in BASS Android and enter the user's six-digit host PIN.
   The phone receives its own revocable credential for this backend.
5. Select **Refresh** to load new phone enrollments in **Users**.

No backend address or Connect action is needed. For local frontend development,
`http://localhost:5173` uses the same host through Vite's proxy to port 5057.
The Android app reaches the authenticated LAN API over HTTPS on port 5056.

---

## Enrolling a user

1. Sign in as an administrator and go to the **Users** tab.
2. Select the backend/device camera as the enrollment source.
3. For a new user, enter a display name and a new six-digit PIN. For an existing
   user, enter their exact display name.
4. Select **Add & enroll face**, then enter your signed-in administrator account's
   PIN. For a new user, the second prompt confirms the new user's PIN entered in
   step 3. The account is created only after the PINs match.
5. Wait for the backend to capture the required samples.

If camera enrollment fails or is cancelled, the account stays in the user list.
Retry with the same display name to enroll that account's face. Removal is a
separate action under **People & Access**.

To reset a PIN, enter the replacement PIN, approve with your signed-in account's
PIN, then confirm the replacement PIN. Resetting a PIN signs that user out on
all devices and invalidates their invitations. Their phones must be paired again.

The face embedding is stored in the selected backend's SQLite database and is available to
the Device API's scan flow.

---

## Face verification (dashboard)

1. Go to the **Control** tab
2. Start the Unlock face scan and enter the signed-in user's PIN.
3. Look at the backend's camera: PC webcam or Pi camera. Access requires the
   configured rolling-window match threshold (6 of 10 observations by default).
4. PC records simulated actuation. On Pi, all lock and ignition outputs remain
   blocked until the serial protocol has command acknowledgement and physical
   position feedback. A PIN alone or uploaded browser/phone verification frames
   cannot grant access.

The current runtime reports that presentation-attack detection is unavailable.
With the persisted liveness setting enabled by default, verification fails
closed before camera capture or actuation. An administrator may explicitly
disable that setting for prototype testing, which reduces security and does not
establish production, vehicle, or presentation-attack safety.

On Pi, startup, shutdown, manual controls, timers, and scan results cannot send
motor or ignition commands. The API reports control unavailable and physical
state unconfirmed. There is no environment-variable bypass. Enrollment and
administrator data tasks may still operate, subject to the liveness policy.

---

## Backend connection

The canonical store is **SQLite on the selected backend** (`db.py` / `db_api.py`).
React and Android read that host's users, settings, and state through the Device
API (`/api/status`, `/api/users`, ...).

### On the Pi

Use `bash scripts/install-wireless-pi.sh` from the repository root. It installs
and enables `faceid-wireless.service`, which runs `bass_wireless.py` as the
single owner of the Pi camera and ESP32 serial connection. Do not run
`pi_device_api.py` directly: it only exports `create_app(...)` for an injected
database/runtime and has no module-level application or public listener.

Runtime overrides such as `ESP32_SERIAL_PORT`, scan timeouts, enrollment sample
interval, `BASS_PORT`, and `BASS_DASHBOARD_PORT` can be placed in
`/etc/default/faceid-wireless`. The old `enroll.py` and `verify_live.py` entrypoints
exit with an error; they cannot run beside or replace the authenticated host.

Authorization adds tables to the existing SQLite database and uses the
device-adjacent `device.auth.key` as its PIN pepper. The Pi installer applies
that additive migration offline. For a manual migration, stop the service and
set the database path explicitly:

```bash
sudo systemctl stop faceid-wireless.service
FACEID_DB_PATH=/home/pi/faceid/faceid.db \
BASS_DEVICE_CONFIG=/home/pi/faceid/device.json \
  .venv-wireless/bin/python bass_wireless.py --mode pi --migrate-security-only
```

Do not run that command against the real Pi database from this worktree: no real
database migration or deployment has been performed here. Back up and restore
`device.json`, `device.tls.pem`, `device.auth.key`, and the SQLite database as
one coherent set. Startup binds that database to the configured device ID and
TLS certificate fingerprint. A mismatch fails closed before hardware or either
listener starts. The installer transfers the `device.auth.key` sidecar when
present, refuses a mismatched destination, and applies the schema and identity
binding offline before starting the service.

Replacing TLS intentionally requires a stopped host and an explicit destructive
authorization rotation. Back up the coherent set, replace `device.tls.pem` by
an operator-controlled process, then run:

```bash
FACEID_DB_PATH=/home/pi/faceid/faceid.db \
  .venv-wireless/bin/python bass_wireless.py --mode pi \
  --config /home/pi/faceid/device.json \
  --rotate-tls-authorization-only
```

This command does not generate or replace a valid TLS file. It binds the
replacement certificate atomically and revokes every phone credential, local
session, operation grant, and pairing invite. After it succeeds, start the host,
sign in locally with an administrator PIN, display the new QR, and issue new
user invites. Do not merely replace TLS and claim phones were re-paired; without
the explicit rotation the host refuses the database/TLS mismatch.

If an existing administrator loses access, keep the service stopped and run the
local interactive recovery tool; it revokes that user's existing devices and
grants:

```bash
.venv-wireless/bin/python scripts/recover-host-admin.py \
  --db /home/pi/faceid/faceid.db \
  --config /home/pi/faceid/device.json \
  --user 'Admin Name'
```

### In the UI

Normal dashboard operation uses `faceid-wireless.service` and its local page at
`http://localhost:5057`; see the [wireless Pi handoff](docs/android-wireless.md#pi-handoff).
Android uses the certificate-pinned HTTPS listener on port 5056.

### Schema (see `db.py`)

- `users`, `auth_logs`, `settings`, `device_state`

Face templates use the strict `BASSF001` numeric format. The runtime does not
deserialize legacy pickle data; existing trusted databases require the separate
[offline face-template migration](docs/android-wireless.md#face-template-migration)
before deployment. The migration creates a validated copy and does not replace
the active database automatically.

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
- Presentation-attack detection / anti-spoofing (photo/video)
- Full vehicle integration  

**Present but safety-gated**

- The ESP32 serial command protocol and integration code remain in the
  repository, but the real Pi runtime blocks all motor and ignition output until
  acknowledgement and position feedback exist. No connected hardware was
  exercised, so this is not physical or production-safety acceptance.

### Prerequisites

- Python 3.8+ (3.11–3.12 recommended on Windows if prebuilt wheels are missing)
- Raspberry Pi (or PC for development)
- A camera attached to the backend: Pi Camera Module on Pi or a USB webcam on PC.
- Virtual environment (required on Pi, recommended on dev PC)

**Python dependencies:** `requirements-http.txt` pins Flask 3.1.3, Werkzeug
3.1.8, Pillow 12.3.0, Cheroot 11.1.2, and pyOpenSSL 26.4.0.
`requirements-pi-device-api.txt` adds `pyserial` and the recognition stack:
`insightface`, `onnxruntime`, OpenCV, and NumPy. Picamera2 is installed by
Raspberry Pi OS with
`sudo apt install python3-picamera2` and exposed to `.venv` via
`--system-site-packages`.

### Running recognition and enrollment

See [Android wireless operation](docs/android-wireless.md) for the PC/Pi host,
pairing, and local dashboard setup. Both UIs ask the backend to capture the face
for unlock. There is no separate Face API listener.

The standalone `enroll.py` and `verify_live.py` CLIs are retired because they
bypass the authenticated host and used the older face-template path. They exit
without opening the camera, serial port, or database. `test_insightface.py` is a
historical experiment only and is not a deployment or migration tool.

### Historical PoC screenshots

These images show the earlier experimental CLI and are not instructions for the
current authenticated host.

**Enrollment**

![Enrollment demo](car_face_auth/images/demo2.png)

**Enrollment terminal output**

![Enrollment terminal](car_face_auth/images/demo1.5.png)

**Verify live**

![Verify live](car_face_auth/images/demo1.png)

---

## Device API routes (`pi_device_api.py`)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/status` | Device status, lock state, battery, signal |
| POST | `/api/unlock` | Retired; use PIN-authorized `/api/scan/start` |
| POST | `/api/lock` | Lock the device |
| POST | `/api/ignition/stop` | Stop ignition |
| POST | `/api/full-reset` | Stop ignition and lock the device |
| GET | `/api/users` | List all users for an administrator or the current user only |
| POST | `/api/users` | Add a new user; `enroll_face: true` also returns a one-use `enrollment_grant` for that user |
| DELETE | `/api/users/<id>` | Remove a user |
| PATCH | `/api/users/<id>/access` | Enable/disable face access for a user |
| POST | `/api/users/<id>/pin` | Reset a user PIN (administrator only) |
| POST | `/api/operation-grants` | Verify current PIN for one action and target |
| POST | `/api/session-login` | Verify the saved phone account's PIN without granting an operation |
| POST | `/api/pairing-invites` | Issue a five-minute user invite (administrator only) |
| GET | `/api/devices` | List paired phones (administrator only) |
| POST | `/api/devices/<id>/revoke` | Revoke one phone (administrator only) |
| POST | `/api/verify-log` | Retired; returns 410 and writes no event |
| POST | `/api/scan/start` | Start a backend-camera unlock or same-driver ignition scan |
| GET | `/api/scan/status?session_id=<id>` | Read scan progress/result |
| POST | `/api/scan/cancel` | Cancel a running backend-camera scan |
| POST | `/api/scan/sample` | Rejected: client images cannot verify unlock/ignition |
| POST | `/api/enroll/start` | Start backend-camera or client-camera enrollment |
| GET | `/api/enroll/status?session_id=<id>` | Read enrollment progress/result |
| POST | `/api/enroll/cancel` | Cancel a running enrollment |
| GET | `/api/logs` | Retrieve auth logs (administrator only) |
| GET | `/api/settings` | Get device settings |
| POST | `/api/settings` | Save device settings |
| GET | `/health` | Process liveness and runtime details |
| GET | `/ready` | Hardware readiness (`503` until camera/model/ESP32 are ready) |

`/health` proves that the API process is alive and includes `runtime_ready` plus
camera/model/ESP32 details. It does not replace an on-Pi hardware acceptance test.
`pi_device_api.py` only defines the injected route factory. `bass_wireless.py`
wraps it with per-device authentication and serves it through bounded Cheroot
11.1.2 workers with pyOpenSSL 26.4.0: Android uses HTTPS port 5056, while the
session-authenticated dashboard bridge is available only on loopback HTTP port
5057. See [wireless API and recovery](docs/android-wireless.md#api-and-recovery)
for its contract. There is no standalone port 5000 listener.

The host rejects requests larger than 9 MiB and JSON bodies larger than 64 KiB.
Client enrollment accepts JPEG only, up to 8 MiB, 4096 pixels on either axis,
and 4,194,304 total pixels. Oversized input returns 413 before the runtime
performs enrollment or a data-changing operation. Client requests to
`POST /api/verify-log` return 410 and cannot create an authoritative event.

The fixed QR supplies TLS-pinned onboarding information only. Pairing also
requires a short-lived administrator-issued invite and the target user's
six-digit host PIN. The host then issues a separate revocable phone credential.
Each sensitive operation requires a fresh 30-second grant bound to the signed-in
user, action, and target. Earlier shared QR keys are rejected for ordinary API
operations.

---

## Tech stack

| Area | Technology |
|------|------------|
| Dashboard | React + Vite + Tailwind CSS |
| Face recognition | InsightFace (buffalo_s model) |
| Device API | Flask route factory behind Cheroot + pyOpenSSL |
| Database | SQLite via db_api.py |
| Camera (Pi) | Picamera2 |
| Hardware control | pyserial → ESP32 |
| Auto-start | systemd |

---

## Hardware integration (ESP32)

Firmware and setup notes live under **`ESP32_Program`** on the `wired-main` branch:

https://github.com/SJSU-CMPE-195/group-project-team-face-id/tree/wired-main/ESP32_Program

The retained ESP32 protocol defines these serial commands:

| Command | Action |
|---------|--------|
| `UNLOCK` | Unlock door |
| `LOCK` | Lock door |
| `START` | Start ignition |
| `STOP` | Stop ignition |

The current Pi runtime does not send these commands. It reports
`actuator_control_available: false`, `physical_state_confirmed: false`, and
`actuator_feedback: "unavailable"`. PC and developer-fixture runtimes may execute
the same state machine only with `simulated_actuators: true`; their results do
not describe physical hardware.
