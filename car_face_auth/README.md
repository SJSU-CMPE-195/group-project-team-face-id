# Car face auth (Python PoC)

Facial-recognition enrollment and development utilities live in this folder. The root `pi_device_api.py` is the deployed Pi runtime and owns camera recognition plus ESP32 lock/ignition commands.

**Full documentation** (repo overview, frontend UI, installation, and running the PoC) is in the [root README](../README.md).

## Proof of concept scope

The proof of concept demonstrates:

- Real-time face detection  
- Face embedding generation  
- Identity verification using a local database  
- Confidence-based access decisions with a rolling window  

**Still prototype-only:**

- Multi-user robustness testing  
- Anti-spoofing (photo/video protection)  
- Authenticated/TLS network control and full vehicle acceptance testing

## Prerequisites

- Python 3.9+ (3.11 recommended)
- Raspberry Pi (or PC for development)  
- Camera (Pi Camera Module or USB webcam)  
- Virtual environment (recommended)  

### Required libraries

- opencv-python  
- numpy  
- insightface  
- onnxruntime (or onnxruntime-gpu)  

Use the `wired-main` branch. Raspberry Pi deployment should use the root `install.sh`; the commands below are only for desktop/CLI development.

## Hardware-free Device API simulator

From the repository root, run `npm run mock:pi`, then set the dashboard Device API
Base URL to `http://localhost:5055`. The simulator reuses the deployed `PiRuntime`
scan, enrollment, authorization, and actuator state machine while replacing the Pi
camera, InsightFace model, and ESP32 serial connection with deterministic seams.
See the root README's **Develop the remote-camera flow without a Pi** section for
scenario and failure-injection examples. Simulator results are development evidence,
not physical hardware acceptance.

## Quick reference — installation and run

```bash
git checkout wired-main
cd car_face_auth
python -m venv venv
# activate venv (Windows: venv\Scripts\activate; macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

Connect your camera and confirm OpenCV can open it.

**Enroll a user**

```bash
python src/enroll.py
```

**Live verification**

```bash
python src/verify_live.py
```

## Expected output (verify live)

- Face bounding boxes  
- Similarity score  
- Status messages such as `ACCESS PENDING` and `ACCESS GRANTED`  

The system uses a rolling window of frames to improve stability and reduce false positives.
