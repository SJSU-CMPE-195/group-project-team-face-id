"""Verify a live face against enrolled users — Pi camera + ESP32 hardware."""

import pickle
import sys
from pathlib import Path
from collections import deque, Counter

import cv2
import numpy as np
from insightface.app import FaceAnalysis
import serial
import serial.tools.list_ports
import time
from picamera2 import Picamera2

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
EMBEDDINGS_FILE = DATA_DIR / "embeddings" / "face_embeddings.pkl"

# Make db_api importable when running from car_face_auth/
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

ESP_KEYWORDS = ["CP210", "CH340", "CH341", "FTDI", "USB-SERIAL", "USB Serial", "Silicon Labs"]


# ── ESP32 helpers ─────────────────────────────────────────────────────────────

def find_esp_port():
    ports = serial.tools.list_ports.comports()
    for port in ports:
        desc = (port.description or "") + (port.manufacturer or "")
        if any(kw.lower() in desc.lower() for kw in ESP_KEYWORDS):
            return port.device
    if ports:
        return ports[0].device
    return None


def connect_esp():
    port = find_esp_port()
    if port is None:
        print("ESP not found. Running without hardware.")
        return None
    try:
        ser = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        ser.reset_input_buffer()
        print(f"Connected to ESP on {port}")
        return ser
    except serial.SerialException as e:
        print(f"Could not connect to ESP: {e}")
        return None


def send_command(ser, cmd):
    if ser is None:
        return
    try:
        ser.write((cmd + "\n").encode())
    except serial.SerialException as e:
        print(f"Serial error sending {cmd}: {e}")


# ── Database ──────────────────────────────────────────────────────────────────

def load_database():
    """Load embeddings from SQLite (preferred) with .pkl fallback."""
    try:
        import db_api
        rows = db_api.get_all_face_encodings()
        db = {}
        for row in rows:
            if row["face_encoding"]:
                db[row["name"]] = pickle.loads(row["face_encoding"])
        if db:
            return db
    except Exception as e:
        print(f"SQLite load failed ({e}), falling back to .pkl")
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}


# ── Recognition helpers ───────────────────────────────────────────────────────

def cosine_similarity(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    return float(np.dot(a, b))


def find_best_match(live_embedding, db):
    best_user = None
    best_score = -1.0
    for user_name, embeddings in db.items():
        for stored_embedding in embeddings:
            score = cosine_similarity(live_embedding, stored_embedding)
            if score > best_score:
                best_score = score
                best_user = user_name
    return best_user, best_score


def evaluate_window(history, min_matches):
    valid_users = [user for matched, user in history if matched and user is not None]
    if not valid_users:
        return False, None, 0
    counts = Counter(valid_users)
    best_user, best_count = counts.most_common(1)[0]
    if best_count >= min_matches:
        return True, best_user, best_count
    return False, best_user, best_count


# ── Main loop ─────────────────────────────────────────────────────────────────

def main():
    db = load_database()
    if not db:
        print("No enrolled users found. Please run enroll.py first")
        return

    print("Loaded enrolled users:", list(db.keys()))

    ser = connect_esp()

    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320, 320))
    print("Model loaded.")

    print("Opening Pi camera.")
    cap = Picamera2()
    cap.configure(cap.create_preview_configuration(main={"format": "RGB888", "size": (640, 480)}))
    cap.start()
    print("Camera opened successfully")

    threshold = 0.45
    window_size = 10
    min_matches = 6

    history = deque(maxlen=window_size)
    access_granted = False
    granted_user = None
    ignition_prompt = False
    ignition_running = False
    current_matched = False

    print("Press 'r' to reset access state and rolling window")
    print("Press 'i' to start ignition (once access is granted)\n")

    while True:
        frame = cap.capture_array()
        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        display_frame = frame.copy()

        faces = app.get(frame)
        current_matched = False

        if len(faces) == 1:
            face = faces[0]
            live_embedding = face.embedding.astype(np.float32)
            best_user, best_score = find_best_match(live_embedding, db)
            matched = best_score >= threshold
            current_matched = matched

            if matched:
                history.append((True, best_user))
                live_label = f"Match: {best_user} | Score: {best_score:.3f}"
                live_color = (0, 255, 0)
            else:
                history.append((False, None))
                live_label = f"Unknown | Score: {best_score:.3f}"
                live_color = (0, 0, 255)

            x1, y1, x2, y2 = face.bbox.astype(int)
            cv2.rectangle(display_frame, (x1, y1), (x2, y2), live_color, 2)

        elif len(faces) == 0:
            live_label = "No face detected"
            live_color = (0, 0, 255)
        else:
            live_label = "Multiple faces detected"
            live_color = (0, 0, 255)

        granted, candidate_user, candidate_count = evaluate_window(history, min_matches)

        if granted and not access_granted:
            access_granted = True
            granted_user = candidate_user
            print(f"ACCESS GRANTED: {granted_user}")
            send_command(ser, "UNLOCK")
            try:
                import db_api
                user_row = next(
                    (u for u in db_api.get_all_users() if u["name"] == granted_user), None
                )
                db_api.log_event(
                    "face_verify", "ok",
                    detail=f"Granted: {granted_user}",
                    user_id=user_row["id"] if user_row else None,
                )
            except Exception:
                pass
            ignition_prompt = True

        if access_granted:
            state_label = f"ACCESS GRANTED: {granted_user}"
            state_color = (0, 255, 0)
        else:
            state_label = "ACCESS PENDING"
            state_color = (0, 255, 255)

        history_label = f"Window: {candidate_count}/{window_size} for {candidate_user or 'None'}"
        threshold_label = f"Threshold: {threshold:.2f} | Need: {min_matches}/{window_size}"

        if ignition_running:
            ignition_label = "IGNITION ON | Press 'r' to reset/stop ignition"
            ignition_color = (0, 255, 255)
        elif ignition_prompt:
            ignition_label = "Press 'i' to start ignition" if current_matched else "Waiting for authorized user..."
            ignition_color = (0, 255, 255) if current_matched else (0, 165, 255)
        else:
            ignition_label = None

        cv2.putText(display_frame, live_label,      (10, 30),  cv2.FONT_HERSHEY_SIMPLEX, 0.7, live_color,      2)
        cv2.putText(display_frame, history_label,   (10, 55),  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.putText(display_frame, threshold_label, (10, 80),  cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 2)
        cv2.putText(display_frame, state_label,     (10, 105), cv2.FONT_HERSHEY_SIMPLEX, 0.6, state_color,     2)
        if ignition_label:
            cv2.putText(display_frame, ignition_label, (10, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.6, ignition_color, 2)

        cv2.imshow("Verify Live", display_frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            send_command(ser, "STOP")
            send_command(ser, "LOCK")
            print("Ignition turned off. Locked.")
            break

        elif key == ord("i"):
            if ignition_prompt and not ignition_running:
                if current_matched and access_granted:
                    send_command(ser, "START")
                    send_command(ser, "LOCK")
                    ignition_running = True
                    ignition_prompt = False
                    print("IGNITION STARTED")
                else:
                    print("Waiting for authorized user to start ignition.")

        elif key == ord("r"):
            if ignition_running:
                send_command(ser, "STOP")
                ignition_running = False
            send_command(ser, "LOCK")
            history.clear()
            access_granted = False
            granted_user = None
            ignition_prompt = False
            print("Verification state reset. Locked and ready for new user.")

    if ser:
        ser.close()
    cap.stop()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
