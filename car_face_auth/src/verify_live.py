import pickle
from pathlib import Path
import cv2
import numpy as np
from insightface.app import FaceAnalysis
from collections import deque, Counter

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

def evaluate_window(history, min_matches):
    #history contain tuples like (matched: bool, user_name: str or None), it returns (granted: bool, granted_user: str or none, match_count: int)
    valid_users = [user for matched, user in history if matched and user is not None]

    if not valid_users:
        return False, None, 0
    
    counts = Counter(valid_users)
    best_user, best_count = counts.most_common(1)[0]

    if best_count >= min_matches:
        return True, best_user, best_count
    
    return False, best_user, best_count

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
    window_size = 10
    min_matches = 6

    history = deque(maxlen=window_size)
    access_granted = False
    granted_user = None

    print("\nVerification started.")
    print("Controls:")
    print("Press 'q' to quit")
    print("Press 'r' to reset access state and rolling window\n")

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Could not read frame.")
            break

        frame = cv2.resize(frame, (640,480))
        display_frame = frame.copy()

        faces = app.get(frame)
        live_label = "No face detected"
        live_color = (0,0,255)

        if len(faces) == 1:
            face = faces[0]
            live_embedding = face.embedding.astype(np.float32)
            
            best_user, best_score = find_best_match(live_embedding, db)
            matched = best_score >= threshold

            if matched:
                history.append((True, best_user))
                live_label = f"Match: {best_user} | Score: {best_score:.3f}"
                live_color = (0,255,0)
            else:
                history.append((False, None))
                live_label = f"Unknown | Score: {best_score:.3f}"
                live_color = (0,0,255)
            
            x1,y1,x2,y2 = face.bbox.astype(int)
            cv2.rectangle(display_frame, (x1,y1), (x2,y2), live_color, 2)

        elif len(faces) > 1:
            live_label = "Multiple faces detected"
            live_color = (0,0,255)
        else:
            live_label = "No face detected"
            live_color = (0,0,255)

        granted, candidate_user, candidate_count = evaluate_window(history, min_matches)

        if granted and not access_granted:
            access_granted = True
            granted_user = candidate_user
            print(f"ACCESS GRANTED: {granted_user}")
            #later, this will send a command to esp
        
        if access_granted:
            state_label = f"ACCESS GRANTED: {granted_user}"
            state_color = (0,255,0)
        else:
            state_label = "ACCESS PENDING"
            state_color = (0,255,255)

        history_label = f"Window: {candidate_count}/{window_size} for {candidate_user if candidate_user else 'None'}"
        threshold_label = f"Threshold: {threshold:.2f} | Need: {min_matches}/{window_size}"

        cv2.putText(
            display_frame,
            live_label,
            (10,30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            live_color,
            2,
        )

        cv2.putText(
            display_frame,
            history_label,
            (10,50),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255,255,255),
            2,
        )

        cv2.putText(
            display_frame,
            threshold_label,
            (10,75),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255,255,255),
            2,
        )

        cv2.putText(
            display_frame,
            state_label,
            (10,100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            state_color,
            2,
        )

        cv2.imshow("Verify Live", display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print("Verification ended.")
            break
        elif key == ord("r"):
            history.clear()
            access_granted = False
            granted_user = None
            print("Verification state reset.")
    
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
