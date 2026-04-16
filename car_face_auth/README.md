Project Description

This project implements a facial-recognition-based vehicle access system using a Raspberry Pi and camera module. The system identifies authorized users in real time and simulates unlocking a vehicle when a valid face is detected with high confidence.

Proof of Concept Scope

The Proof of Concept demonstrates real-time face detection, embedding generation, and identity verification using a local database of enrolled users. It includes a confidence-based access decision system with temporal smoothing (rolling window).
Not included yet are hardware-controlled door locks/ignition, multi-user robustness testing, anti-spoofing mechanisms, and full system integration with vehicle electronics.

Prerequisites
Python 3.8+
Raspberry Pi (or PC for development)
Camera (Raspberry Pi Camera Module or USB webcam)
Virtual environment (recommended)

Required Python libraries:

opencv-python
numpy
insightface
onnxruntime (or onnxruntime-gpu if supported)
pickle (standard library)
Installation

Clone the repository:

git clone <your-repo-url>
cd <your-repo-name>

Create and activate a virtual environment:

python -m venv venv
source venv/bin/activate   # Mac/Linux
venv\Scripts\activate      # Windows

Install dependencies:

pip install -r requirements.txt
Ensure your camera is connected and accessible.
Running the PoC
Step 1: Enroll a User

Run the enrollment script to capture and store face embeddings:

python enroll.py
Enter a username when prompted
The system will capture multiple frames and store embeddings locally
Step 2: Run Live Verification

Start the real-time recognition system:

python verify_live.py
What You Should See:
Bounding boxes around detected faces
A similarity score for the detected user
Status labels such as:
ACCESS PENDING (collecting frames)
ACCESS GRANTED
ACCESS DENIED

The system uses a rolling window of recent frames to stabilize decisions and reduce false positives.

Demo
![Demo](images/demo1.png)
![Demo](images/demo1.5.png)
![Demo](images/demo2.png)


Technical Stack
Language: Python
Computer Vision: OpenCV
Face Recognition: InsightFace (Buffalo_L model)
Numerical Computing: NumPy
Model Runtime: ONNX Runtime
Hardware: Raspberry Pi + Camera Module
Data Storage: Pickle (for embedding database)

Plan for 195B
Integrate with physical hardware (door lock + ignition control)
Improve robustness to lighting, angles, and partial occlusion
Add anti-spoofing (photo/video attack prevention)
Optimize performance for real-time Raspberry Pi deployment
Expand user testing and evaluate system accuracy under real-world conditions