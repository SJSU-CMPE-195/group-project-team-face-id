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
│   ├── faceid-api.service
│   └── faceid-verify.service
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

### Browser + local Face API (dev PC)

Use this when you want **your laptop webcam** in the UI to talk to **InsightFace** on the same machine (no Pi required for this path).

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

3. In the app, open **Control** → **Connection** (scroll on the Control page). Set **Face API** to `http://127.0.0.1:8765` (saved in `localStorage`).

4. **Enroll**  
   - **Users** → enter a **Display name** → **Start face enrollment** → turn on the camera → **Capture sample** ten times (one face in frame) → **Save to face database**. Embeddings are stored in the SQLite database on the Pi.

5. **Verify**  
   - **Control** → **Turn on camera**. The UI sends JPEG frames to `/api/verify-frame` and shows match / rolling-window status.  
   - If the API returns `400`, the face database is empty — enroll first (step 4 or CLI below).

**Device mode (Pi):** toggle **Device API mode** and set **Base URL** to your Pi (for example `http://192.168.4.1:5000`).

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
| **Overview** | `src/components/Overview.jsx` | Mode / device / safety summary |
| **StatusPanel** | `src/components/StatusPanel.jsx` | Lock, battery, signal, unlock |
| **ConnectionPanel** | `src/components/ConnectionPanel.jsx` | Sim vs device, Pi base URL, Face API URL |
| **Tabs** | `src/components/Tabs.jsx` | Tab strip helper where used |
| **ControlTab** | `src/components/ControlTab.jsx` | Browser camera + live verify via Face API |
| **UsersTab** | `src/components/UsersTab.jsx` | Sim/Pi user list + browser face enrollment |
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

- **Simulation vs device**: demo state vs Pi HTTP API (`useApi`).
- **Local Face API URL**: browser webcam enrollment and verification against InsightFace.
- **Persistent UI state**: `localStorage` (mode, base URL, face API URL, sim snapshot).
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
git checkout fully-wired-main

# Run the install script — handles everything automatically
bash install.sh
```

The install script will:
- Create the SQLite database at `~/faceid/faceid.db`
- Install all Python dependencies into a virtual environment
- Add your user to the `dialout`, `video`, and `gpio` groups
- Install and enable systemd services so everything starts on boot

To verify services are running after install:
```bash
sudo systemctl status faceid-api
sudo systemctl status faceid-verify
```

---

## Setup — Dashboard (Mac / Windows / Linux)

The React UI only needs Node.js. Run this from your laptop on the same WiFi network as the Pi.

### Mac / Linux

```bash
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
git checkout fully-wired-main
npm install
npm run dev
```

### Windows

```cmd
git clone https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
git checkout fully-wired-main
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## Running the full system

Once the Pi is set up and the UI is running on your laptop:

1. Open [http://localhost:5173](http://localhost:5173)
2. Go to the **Connection** panel
3. Set **Base URL** to `http://<pi-ip>:5000`
4. Set **Face API URL** to `http://<pi-ip>:8765`
5. Switch to **Device mode** and hit **Refresh** — should show Online

To find your Pi's IP address, run `hostname -I` on the Pi.

---

## Enrolling a user

1. Go to the **Users** tab
2. Enter a name and click **Add**
3. Click **Start face enrollment**
4. Allow camera access in your browser
5. Click **Capture sample** 10 times, moving your head slightly between each
6. Click **Save to face database**

The face embedding is stored in the SQLite database on the Pi. The user can now be recognized by both the dashboard and `verify_live.py`.

---

## Face verification (dashboard)

1. Go to the **Control** tab
2. Click **Turn on camera & scan**
3. Look at the camera — after 6 matches in a 10-frame rolling window, access is granted
4. The Pi sends `UNLOCK` to the ESP32

---

## Device mode

The canonical store is **SQLite on the Pi** (`db.py` / `db_api.py`). The React app talks to the Pi over HTTP using the same routes as before (`/api/status`, `/api/users`, …).

### On the Pi

1. Initialize the database (creates tables under `FACEID_DB_PATH`, default `/home/pi/faceid/faceid.db`):

   ```bash
   python db.py
   ```

2. Install Flask and start the device API (listens on `0.0.0.0`, port **5000** by default):

   ```bash
   pip install -r requirements-pi-device-api.txt
   python pi_device_api.py
   ```

   Optional environment variables:

   - `FACEID_DB_PATH` — SQLite file path (default: `/home/pi/faceid/faceid.db`)
   - `PORT` — HTTP port (default: `5000`)

### In the UI

- Switch mode to **device**
- Set **Base URL** to your Pi, e.g. `http://192.168.4.1:5000` (no trailing slash)
- Refresh

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
- Camera (Pi Camera Module or USB webcam) for **CLI** scripts; for **browser** enrollment/verify, the laptop webcam is enough.
- Virtual environment (required on Pi, recommended on dev PC)

**Python dependencies** (`car_face_auth/requirements.txt`): `insightface`, `onnxruntime`, `opencv-python`, `numpy`, `fastapi`, `uvicorn[standard]`, `python-multipart`, `picamera2`, `pyserial`.

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

**Option B — HTTP API + React (browser camera)**

See the **Browser + local Face API** subsection under [Frontend](#frontend-react-ui): run `uvicorn` on `src.api_server:app` port **8765**, then use **Users** (enroll) and **Control** (verify) in the UI.

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
| POST | `/api/unlock` | Unlock the device |
| POST | `/api/lock` | Lock the device |
| GET | `/api/users` | List all enrolled users |
| POST | `/api/users` | Add a new user |
| DELETE | `/api/users/<id>` | Remove a user |
| PATCH | `/api/users/<id>/access` | Enable/disable face access for a user |
| PATCH | `/api/users/<id>/embedding` | Save face embedding to SQLite |
| GET | `/api/users/<id>/embedding` | Retrieve face embedding |
| POST | `/api/verify-log` | Log a face verify event |
| GET | `/api/logs` | Retrieve auth logs |
| GET | `/api/settings` | Get device settings |
| POST | `/api/settings` | Save device settings |

---

## Tech stack

| Area | Technology |
|------|------------|
| Dashboard | React + Vite + Tailwind CSS |
| Face recognition | InsightFace (buffalo_s model) |
| Face API | FastAPI + Uvicorn |
| Device API | Flask |
| Database | SQLite via db_api.py |
| Camera (Pi) | Picamera2 |
| Hardware control | pyserial → ESP32 |
| Auto-start | systemd |

---

## Hardware integration (ESP32)

Firmware and setup notes live under **`ESP32_Program`** (see branch **`Hardware-Integration`** on GitHub):

https://github.com/SJSU-CMPE-195/group-project-team-face-id/tree/Hardware-Integration/ESP32_Program

The Pi sends simple serial commands to the ESP32:

| Command | Action |
|---------|--------|
| `UNLOCK` | Unlock door |
| `LOCK` | Lock door |
| `START` | Start ignition |
| `STOP` | Stop ignition |
