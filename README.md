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
face-ui/
  src/
    components/           # UI (SidebarNav, TopBar, tabs, cards, …)
    context/              # ThemeProvider / theme hooks
    hooks/                # Custom React hooks (state & logic)
    utils/                # Helper functions
    App.jsx               # Root layout
    main.jsx              # Entry point
  car_face_auth/          # Python PoC + optional local HTTP API
    src/
      enroll.py           # CLI enrollment (OpenCV window)
      verify_live.py      # CLI live verification
      api_server.py       # FastAPI: browser frames → InsightFace
      face_engine.py      # Shared embeddings DB + inference helpers
    requirements.txt
    images/               # Demo screenshots
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
   - **Users** → enter a **Display name** → **Start face enrollment** → turn on the camera → **Capture sample** ten times (one face in frame) → **Save to face database**.  
   - This writes the same pickle file as the CLI: `car_face_auth/data/embeddings/face_embeddings.pkl`.

5. **Verify**  
   - **Control** → **Turn on camera**. The UI sends JPEG frames to `/api/verify-frame` and shows match / rolling-window status.  
   - If the API returns `400`, the face database is empty—enroll first (step 4 or CLI below).

**Device mode (Pi):** toggle **Device API mode** and set **Base URL** to your Pi (for example `http://192.168.4.1:5000` if using the team Flask service). That path is separate from the local Face API on port 8765.

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
| **useAppState** | `src/hooks/useAppState.js` | Mode, sim, device cache, `faceApiUrl`, settings, `localStorage` |
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

## Face recognition backend (Python PoC)

Facial-recognition vehicle access using a Raspberry Pi and camera: real-time detection, embeddings, local identity checks, and confidence-based unlock decisions (simulated).

### Proof-of-concept scope

**Included**

- Real-time face detection  
- Face embedding generation  
- Identity verification against a local database  
- Confidence-based access with a rolling window  

**Not in scope yet**

- Physical door lock / ignition control  
- Multi-user robustness testing  
- Anti-spoofing (photo/video)  
- Full vehicle integration  

### Prerequisites

- Python 3.8+ (3.11–3.12 recommended on Windows if prebuilt wheels are missing; 3.14 may require MSVC Build Tools for some packages).  
- Raspberry Pi (or PC for development)  
- Camera (Pi Camera Module or USB webcam) for **CLI** scripts; for **browser** enrollment/verify, the laptop webcam is enough.  
- Virtual environment (recommended)  

**Python dependencies** (`car_face_auth/requirements.txt`): `insightface`, `onnxruntime`, `opencv-python`, `numpy`, `fastapi`, `uvicorn[standard]`, `python-multipart`. Use `onnxruntime-gpu` instead of `onnxruntime` only if you configure GPU.

**Branch note:** the full Python PoC is maintained on the **Machine-Learning** branch. Check out that branch and work from `car_face_auth/`.

### Installation

1. Clone the repo and enter the project root.

2. Switch to the Machine-Learning branch and enter `car_face_auth`:

   ```bash
   git checkout Machine-Learning
   cd car_face_auth
   ```

3. Create and activate a virtual environment:

   ```bash
   python -m venv venv
   ```

   - macOS / Linux: `source venv/bin/activate`  
   - Windows: `venv\Scripts\activate`

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. For **CLI** OpenCV scripts, ensure a camera is free (close Teams, browser tabs using the camera, etc.). On Windows, if `verify_live` / `enroll` exits immediately with MSMF errors, try another camera index or search for OpenCV `CAP_DSHOW` workarounds.

### Running the PoC

**Option A — CLI (OpenCV window)**

1. **Enroll** (from `car_face_auth/`):

   ```bash
   python src/enroll.py
   ```

   Enter a username when prompted. Press **`s`** in the **OpenCV window** (not the terminal) to save each sample; you need **10** samples. Embeddings are stored under `car_face_auth/data/embeddings/face_embeddings.pkl`.

   If you see `Unable to import dependency onnxruntime`, run `pip install onnxruntime` inside the same venv (it is listed in `requirements.txt` for fresh installs).

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

### Python stack

| Area | Technology |
|------|------------|
| Language | Python |
| Computer vision | OpenCV |
| Face recognition | InsightFace (buffalo_s) |
| Numerics | NumPy |
| Runtime | ONNX Runtime |
| Optional HTTP | FastAPI + Uvicorn (`src/api_server.py`) |
| Hardware | Raspberry Pi + camera (CLI); webcam (browser path) |
| Embeddings store | Pickle (`data/embeddings/face_embeddings.pkl`) |

### Roadmap (CMPE 195B direction)

- Integrate physical door lock and ignition  
- Improve robustness to lighting and head pose  
- Anti-spoofing  
- Raspberry Pi performance tuning  
- User testing and accuracy evaluation  
