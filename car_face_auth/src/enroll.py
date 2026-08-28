"""Enroll a user by capturing multiple face embeddings from the Pi camera."""

import pickle
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis
from picamera2 import Picamera2


DATA_DIR = Path("data")
EMBEDDINGS_DIR = DATA_DIR / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "face_embeddings.pkl"
SAMPLES_NEEDED = 10
WINDOW_NAME = "Enroll User"


def load_database():
    """Load the saved face embedding database if it exists."""
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as file:
            return pickle.load(file)
    return {}


def save_database(db):
    """Save the face embedding database to disk."""
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_FILE, "wb") as file:
        pickle.dump(db, file)


def setup_model():
    """Initialize and prepare the InsightFace model."""
    print("Loading InsightFace model...")
    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320, 320))
    print("Model loaded.")
    return app


def setup_camera():
    """Open the Pi camera via picamera2."""
    print("Starting Pi camera...")
    cap = Picamera2()
    cap.configure(cap.create_preview_configuration(main={"format": "RGB888", "size": (640, 480)}))
    cap.start()
    print("Camera opened successfully.")
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    return cap


def print_instructions():
    """Print enrollment instructions to the terminal."""
    print("\nEnrollment started.")
    print("Instructions:")
    print("1. Make sure only one face is visible in the camera.")
    print("2. Press 's' to save a sample when your face is detected.")
    print(
        "3. Press 'q' to quit early "
        f"(you need at least {SAMPLES_NEEDED} samples to enroll).\n"
    )


def collect_embeddings(app, cap):
    """Collect face embeddings from Pi camera frames."""
    collected_embeddings = []
    enrollment_cancelled = False

    try:
        while len(collected_embeddings) < SAMPLES_NEEDED:
            frame = cap.capture_array()
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            display_frame = frame.copy()

            status_text = (
                f"Samples: {len(collected_embeddings)}/{SAMPLES_NEEDED}, "
                "press 's' to save, 'q' to quit"
            )

            cv2.putText(
                display_frame,
                status_text,
                (20, 70),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2,
            )
            cv2.imshow(WINDOW_NAME, display_frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("Enrollment cancelled. No samples saved.")
                enrollment_cancelled = True
                break

            if key == ord("s"):
                print("Processing captured frame...")
                faces = app.get(frame)

                if len(faces) == 1:
                    embedding = faces[0].embedding.astype(np.float32)
                    collected_embeddings.append(embedding)
                    print(f"Sample {len(collected_embeddings)}/{SAMPLES_NEEDED} collected.")
                elif len(faces) == 0:
                    print("No face detected. Please try again.")
                else:
                    print("Multiple faces detected. Please ensure only one face is visible.")
    finally:
        cap.stop()
        cv2.destroyAllWindows()

    return collected_embeddings, enrollment_cancelled


def main():
    """Run the user enrollment process."""
    user_name = input("Enter user's name to enroll: ").strip()
    if not user_name:
        print("User name cannot be empty.")
        return

    app = setup_model()
    cap = setup_camera()

    print_instructions()
    collected_embeddings, enrollment_cancelled = collect_embeddings(app, cap)

    if enrollment_cancelled:
        return

    if len(collected_embeddings) < SAMPLES_NEEDED:
        print("Not enough samples collected. No samples saved.")
        return

    db = load_database()
    db[user_name] = collected_embeddings
    save_database(db)

    print(
        f"\nEnrollment complete for '{user_name}'. "
        f"{len(collected_embeddings)} samples saved."
    )
    print(f"Database file: {EMBEDDINGS_FILE}")


if __name__ == "__main__":
    main()