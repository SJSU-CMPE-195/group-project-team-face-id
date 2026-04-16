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
    components/           # Reusable UI components
    hooks/                # Custom React hooks (state & logic)
    utils/                # Helper functions
    App.jsx               # Root container component
    main.jsx              # Entry point
  car_face_auth/          # Python PoC (InsightFace, OpenCV, ONNX)
    enroll.py
    verify_live.py
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

### Components reference

| Component | Location | Purpose |
|-----------|----------|---------|
| **Badge** | `src/components/Badge.jsx` | Small inline label with neutral styling |
| **Card** | `src/components/Card.jsx` | Rounded card container with shadow |
| **Btn** | `src/components/Btn.jsx` | Button with primary/secondary/danger variants |
| **Input** | `src/components/Input.jsx` | Text/number input field |
| **Switch** | `src/components/Switch.jsx` | Toggle switch for boolean settings |
| **TabBtn** | `src/components/TabBtn.jsx` | Tab button (control, users, logs, settings) |
| **Toast** | `src/components/Toast.jsx` | Notification popup (success/error/info) |
| **Header** | `src/components/Header.jsx` | Page title, description, and refresh button |
| **Overview** | `src/components/Overview.jsx` | Three info cards (Mode, Device, Safety) |
| **StatusPanel** | `src/components/StatusPanel.jsx` | Lock state, battery, and signal status |
| **ConnectionPanel** | `src/components/ConnectionPanel.jsx` | Mode toggle, base URL input, demo story |
| **Tabs** | `src/components/Tabs.jsx` | Navigation tabs for content sections |
| **ControlTab** | `src/components/ControlTab.jsx` | Camera placeholder and quick action buttons |
| **UsersTab** | `src/components/UsersTab.jsx` | Enroll/remove users (sim mode only) |
| **LogsTab** | `src/components/LogsTab.jsx` | Event log viewer with clear button |
| **SettingsTab** | `src/components/SettingsTab.jsx` | Core settings (re-lock, liveness, lockout) |

### Hooks reference

| Hook | Location | Purpose |
|------|----------|---------|
| **useAppState** | `src/hooks/useAppState.js` | Central state (mode, sim, status, settings, etc.) + localStorage sync |
| **useAppActions** | `src/hooks/useAppActions.js` | Business logic (refresh, unlock, enroll, delete user, save settings) |
| **useApi** | `src/hooks/useApi.js` | API / simulation layer (device vs. sim mode) |

### Utils reference

| Utility | Location | Purpose |
|---------|----------|---------|
| **clamp** | `src/utils/helpers.js` | Clamp a number between min and max |
| **genId** | `src/utils/helpers.js` | Generate unique IDs for toasts, users, logs |
| **fmt** | `src/utils/helpers.js` | Format timestamp to locale string |

### Frontend features

- **Dual mode**: simulation for demo, device API for production
- **Persistent state**: data saved to `localStorage`
- **Component-based**: reusable UI pieces
- **Lightweight app**: `App.jsx` as a thin container
- **Modular logic**: state and actions in custom hooks
- **Tabs**: Control, Users, Logs, Settings

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

- Python 3.8+  
- Raspberry Pi (or PC for development)  
- Camera (Pi Camera Module or USB webcam)  
- Virtual environment (recommended)  

**Python dependencies** (see `car_face_auth/requirements.txt`): `opencv-python`, `numpy`, `insightface`, `onnxruntime` (or `onnxruntime-gpu`).

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

5. Connect and verify your camera is available to OpenCV.

### Running the PoC

**1. Enroll a user**

```bash
python enroll.py
```

- Enter a username when prompted.  
- The system captures multiple frames and stores face embeddings locally.
- If error occurs such as "Unable to import dependency onnxruntime", you need to run 'pip install onnxruntime' in the venv.

**2. Live verification**

```bash
python verify_live.py
```

You should see face bounding boxes, similarity scores, and status messages such as `ACCESS PENDING` and `ACCESS GRANTED`. A rolling window of frames improves stability and reduces false positives.

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
| Face recognition | InsightFace (Buffalo_s) |
| Numerics | NumPy |
| Runtime | ONNX Runtime |
| Hardware | Raspberry Pi + camera |
| Embeddings store | Pickle (local database) |

### Roadmap (CMPE 195B direction)

- Integrate physical door lock and ignition  
- Improve robustness to lighting and head pose  
- Anti-spoofing  
- Raspberry Pi performance tuning  
- User testing and accuracy evaluation  
