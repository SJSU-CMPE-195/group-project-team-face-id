"""Verify a live face against enrolled users using Raspberry Pi camera input."""

from collections import Counter, deque
import pickle
from pathlib import Path

import cv2
import numpy as np
from insightface.app import FaceAnalysis
from picamera2 import Picamera2

DATA_DIR = Path("data")
EMBEDDINGS_FILE = DATA_DIR / "embeddings" / "face_embeddings.pkl"
WINDOW_NAME = "Verify Live"

THRESHOLD = 0.45
WINDOW_SIZE = 10
MIN_MATCHES = 6


def load_database():
    """Load the enrolled face embeddings database."""
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as file:
            return pickle.load(file)
    return {}


def cosine_similarity(vector_a, vector_b):
    """Compute cosine similarity between two embedding vectors."""
    vector_a = vector_a / np.linalg.norm(vector_a)
    vector_b = vector_b / np.linalg.norm(vector_b)
    return float(np.dot(vector_a, vector_b))


def find_best_match(live_embedding, database):
    """Find the best matching enrolled user for a live embedding."""
    best_user = None
    best_score = -1.0

    for user_name, embeddings in database.items():
        for stored_embedding in embeddings:
            score = cosine_similarity(live_embedding, stored_embedding)
            if score > best_score:
                best_score = score
                best_user = user_name

    return best_user, best_score


def evaluate_window(history, min_matches):
    """Evaluate rolling history and decide whether access should be granted."""
    valid_users = [user for matched, user in history if matched and user is not None]

    if not valid_users:
        return False, None, 0

    counts = Counter(valid_users)
    best_user, best_count = counts.most_common(1)[0]

    if best_count >= min_matches:
        return True, best_user, best_count

    return False, best_user, best_count


def setup_model():
    """Initialize and prepare the InsightFace model."""
    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320, 320))
    print("Model loaded.")
    return app


def setup_camera():
    """Open the Raspberry Pi camera using Picamera2."""
    print("Opening camera.")
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": (640, 480), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    print("Camera opened successfully")
    return picam2


def process_single_face(face, database, history):
    """Process one detected face and update match history."""
    live_embedding = face.embedding.astype(np.float32)
    best_user, best_score = find_best_match(live_embedding, database)
    matched = best_score >= THRESHOLD

    if matched:
        history.append((True, best_user))
        live_label = f"Match: {best_user} | Score: {best_score:.3f}"
        live_color = (0, 255, 0)
    else:
        history.append((False, None))
        live_label = f"Unknown | Score: {best_score:.3f}"
        live_color = (0, 0, 255)

    return live_label, live_color


def draw_face_box(display_frame, face, color):
    """Draw a bounding box around the detected face."""
    x1, y1, x2, y2 = face.bbox.astype(int)
    cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)


def draw_status_text(
    display_frame,
    live_label,
    live_color,
    candidate_count,
    candidate_user,
    access_granted,
    granted_user,
):
    """Draw all status text overlays on the frame."""
    if access_granted:
        state_label = f"ACCESS GRANTED: {granted_user}"
        state_color = (0, 255, 0)
    else:
        state_label = "ACCESS PENDING"
        state_color = (0, 255, 255)

    history_label = (
        f"Window: {candidate_count}/{WINDOW_SIZE} for "
        f"{candidate_user if candidate_user else 'None'}"
    )
    threshold_label = (
        f"Threshold: {THRESHOLD:.2f} | Need: {MIN_MATCHES}/{WINDOW_SIZE}"
    )

    cv2.putText(
        display_frame,
        live_label,
        (10, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        live_color,
        2,
    )

    cv2.putText(
        display_frame,
        history_label,
        (10, 50),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        2,
    )

    cv2.putText(
        display_frame,
        threshold_label,
        (10, 75),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        2,
    )

    cv2.putText(
        display_frame,
        state_label,
        (10, 100),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        state_color,
        2,
    )


def reset_verification(history):
    """Reset verification state and rolling window history."""
    history.clear()
    print("Verification state reset.")
    return False, None


def main():
    """Run live face verification using the Raspberry Pi camera."""
    database = load_database()
    if not database:
        print("No enrolled users found. Please run enroll.py first")
        return

    print("Loaded enrolled users:", list(database.keys()))

    app = setup_model()
    picam2 = setup_camera()

    history = deque(maxlen=WINDOW_SIZE)
    access_granted = False
    granted_user = None

    print("\nVerification started.")
    print("Controls:")
    print("Press 'q' to quit")
    print("Press 'r' to reset access state and rolling window\n")

    try:
        while True:
            frame = picam2.capture_array()
            if frame is None:
                print("Could not read frame.")
                break

            # Convert Picamera2 RGB output to BGR for OpenCV/InsightFace usage
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            display_frame = frame.copy()

            faces = app.get(frame)
            live_label = "No face detected"
            live_color = (0, 0, 255)

            if len(faces) == 1:
                face = faces[0]
                live_label, live_color = process_single_face(face, database, history)
                draw_face_box(display_frame, face, live_color)
            elif len(faces) > 1:
                live_label = "Multiple faces detected"
                live_color = (0, 0, 255)

            granted, candidate_user, candidate_count = evaluate_window(
                history,
                MIN_MATCHES,
            )

            if granted and not access_granted:
                access_granted = True
                granted_user = candidate_user
                print(f"ACCESS GRANTED: {granted_user}")

            draw_status_text(
                display_frame,
                live_label,
                live_color,
                candidate_count,
                candidate_user,
                access_granted,
                granted_user,
            )

            cv2.imshow(WINDOW_NAME, display_frame)
            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                print("Verification ended.")
                break
            if key == ord("r"):
                access_granted, granted_user = reset_verification(history)

    finally:
        picam2.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()