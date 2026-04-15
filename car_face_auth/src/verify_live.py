import pickle
from pathlib import Path
import cv2
import numpy as np
from insightface.app import FaceAnalysis

DATA_DIR = Path("data")
EMBEDDINGS_FILE = DATA_DIR/ "embeddings" / "face_embeddings.pkl"

def load_database():
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def cosine_similarity(a,b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    return float(np.dot(a,b))

def find_best_match(live_embedding, db):
    best_user = None
    best_score = -1.0

    for user_name, embeddings in db.items():
        for stored_embeddings in embeddings:
            score = cosine_similarity(live_embedding, stored_embeddings)
            if score > best_score:
                best_score = score
                best_user = user_name
    
    return best_user, best_score

def main():
    db = load_database()
    if not db:
        print("No enrolled users found. Please run enroll.py first")
        return
    
    print("Loaded enrolled users:", list(db.keys()))

    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320,320))
    print("Model Loaded.")

    print("Opening Camera.")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open camera")
        return
    print("Camera opened successfully")

    threshold = 0.45

    print("\nVerification started.")
    print("Press 'q' to quit\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Could not read frame.")
            break

        frame = cv2.resize(frame, (640,480))
        display_frame = frame.copy()

        faces = app.get(frame)
        label = "No face detected"

        if len(faces) == 1:
            face = faces[0]
            live_embedding = face.embedding.astype(np.float32)
            
            best_user, best_score = find_best_match(live_embedding, db)

            if best_score >= threshold:
                label = f"Authorized: {best_user} | Score: {best_score:.3f}"
                color = (0,255,0)
            else:
                label = f"Unknown | Score : {best_score:.3f}"
                color = (0,0,255)
            
            x1,y1,x2,y2 = face.bbox.astype(int)
            cv2.rectangle(display_frame, (x1,y1), (x2,y2), color, 2)

        elif len(faces) > 1:
            label = "Multiple faces detected"
            color = (0,0,255)
        else:
            color = (0,0,255)

        cv2.putText(
            display_frame,
            label,
            (10,30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
        )

        cv2.putText(
            display_frame,
            f"Threshold: {threshold:.2f}",
            (10,65),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255,255,255),
            2,
        )

        cv2.imshow("Verify Live", display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print("Verification ended.")
            break
    
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
