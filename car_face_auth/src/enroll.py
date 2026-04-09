import os
import pickle 
from pathlib import Path
import cv2
import numpy as np
from insightface.app import FaceAnalysis

DATA_DIR = Path("data")
EMBEDDINGS_DIR = DATA_DIR / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "face_embeddings.pkl"

def load_database():
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def save_database(db):
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_FILE, "wb") as f:
        pickle.dump(db, f)

def main():
    user_name = input("Enter user's name to enroll: ").strip()
    if not user_name:
        print("User name cannot be empty.")
        return
    
    samples_needed = 10
    collected_embeddings = []
    
    print("Loading InsightFace model.")
    app = FaceAnalysis(name="buffalo_s", allowed_modules=["detection", "recognition"], providers=["CPUExecutionProvider"],)
    app.prepare(ctx_id=-1, det_size=(320, 320))
    print("Model loaded.")

    print("Starting camera...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open camera")
        return
    print("Camera opened successfully.")

    cv2.namedWindow("Enroll User", cv2.WINDOW_NORMAL)
    
    print("\nEnrollment started.")
    print("Instructions:")
    print("1. Make sure only one face is visible in the camera.")
    print("2. Press 's' to save a sample when your face is detected.")
    print("3. Press 'q' to quit early (you need at least 10 samples to enroll).\n")

    while len(collected_embeddings) < samples_needed:
        ret, frame = cap.read()
        if not ret:
            print("Can't receive frame (stream end?). Exiting ...")
            break

        frame = cv2.resize(frame, (640, 480))
        display_frame = frame.copy()

        status_text = f"Samples: {len(collected_embeddings)}/{samples_needed}, press 's' to save, 'q' to quit"
        
        cv2.putText(display_frame, status_text, (20,70), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cv2.imshow("Enroll User", display_frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print("Enrollment cancelled.")
            break

        if key == ord("s"):
            print("Processing captured frame...")
            faces = app.get(frame)

            if len(faces) == 1:
                embedding = faces[0].embedding.astype(np.float32)
                collected_embeddings.append(embedding)
                print(f"Sample {len(collected_embeddings)}/{samples_needed} collected.")
            elif len(faces) == 0:
                print("No face detected. Please try again.")
            else:
                print("Multiple faces detected. Please ensure only one face is visible.")

    cap.release()
    cv2.destroyAllWindows()

    if not collected_embeddings:
        print("No samples collected. Nothing saved.")
        return
    
    db = load_database()
    db[user_name] = collected_embeddings
    save_database(db)

    print(f"\nEnrollment complete for '{user_name}'. {len(collected_embeddings)} samples saved.")
    print(f"Database file : {EMBEDDINGS_FILE}")

if __name__ == "__main__":
    main()

