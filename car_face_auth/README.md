# Car face auth (Python PoC)

Facial-recognition vehicle access: Raspberry Pi (or desktop) enrollment and live verification live in this folder. The system identifies authorized users in real time and simulates unlocking when a valid face is detected with high confidence.

**Full documentation** (repo overview, frontend UI, installation, and running the PoC) is in the [root README](../README.md).

## Proof of concept scope

The proof of concept demonstrates:

- Real-time face detection  
- Face embedding generation  
- Identity verification using a local database  
- Confidence-based access decisions with a rolling window  

**Not included yet:**

- Physical door lock / ignition control  
- Multi-user robustness testing  
- Anti-spoofing (photo/video protection)  
- Full vehicle system integration  

## Prerequisites

- Python 3.8+  
- Raspberry Pi (or PC for development)  
- Camera (Pi Camera Module or USB webcam)  
- Virtual environment (recommended)  

### Required libraries

- opencv-python  
- numpy  
- insightface  
- onnxruntime (or onnxruntime-gpu)  

**Branch note:** use the **Machine-Learning** branch and work from `car_face_auth/` so paths and dependencies match this PoC.

## Quick reference — installation and run

```bash
git checkout Machine-Learning   # branch that carries this PoC
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
