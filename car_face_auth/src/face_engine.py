"""Shared InsightFace helpers for the HTTP verify API (same DB as enroll.py)."""

from __future__ import annotations

import pickle
import sys
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

CAR_FACE_AUTH_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CAR_FACE_AUTH_ROOT / "data"
EMBEDDINGS_DIR = DATA_DIR / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "face_embeddings.pkl"

# Make db_api importable when running from the car_face_auth directory
_REPO_ROOT = CAR_FACE_AUTH_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

SAMPLES_NEEDED = 10
THRESHOLD = 0.45
WINDOW_SIZE = 10
MIN_MATCHES = 6


def load_database() -> dict:
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
    except Exception:
        pass
    # fallback to .pkl
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}


def save_database(db: dict) -> None:
    """Save embeddings to .pkl (legacy / standalone path)."""
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_FILE, "wb") as f:
        pickle.dump(db, f)


def save_user_embedding(name: str, embeddings: list) -> None:
    """Persist embeddings into SQLite. Creates the user row if it doesn't exist."""
    import uuid, time
    import db_api
    blob = pickle.dumps([e.astype(np.float32) for e in embeddings])
    with db_api.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, name, face_encoding, active, face_access, created_at) VALUES (?,?,?,1,1,?)",
            (str(uuid.uuid4()), name, blob, int(time.time())),
        )
        conn.execute(
            "UPDATE users SET face_encoding=? WHERE name=? AND active=1",
            (blob, name),
        )


def cosine_similarity(vector_a: np.ndarray, vector_b: np.ndarray) -> float:
    vector_a = vector_a / np.linalg.norm(vector_a)
    vector_b = vector_b / np.linalg.norm(vector_b)
    return float(np.dot(vector_a, vector_b))


def find_best_match(live_embedding: np.ndarray, database: dict) -> Tuple[Optional[str], float]:
    best_user = None
    best_score = -1.0
    for user_name, embeddings in database.items():
        for stored_embedding in embeddings:
            score = cosine_similarity(live_embedding, stored_embedding)
            if score > best_score:
                best_score = score
                best_user = user_name
    return best_user, best_score


def decode_image_bytes(data: bytes) -> np.ndarray | None:
    if not data:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return frame


def extract_single_face_embedding(app, frame_bgr: np.ndarray) -> Tuple[Optional[np.ndarray], int]:
    """Return (embedding, face_count). Embedding is set only when exactly one face is found."""
    if frame_bgr is None:
        return None, 0
    frame = cv2.resize(frame_bgr, (640, 480))
    faces = app.get(frame)
    n = len(faces)
    if n != 1:
        return None, n
    return faces[0].embedding.astype(np.float32), 1


def analyze_frame(app, frame_bgr: np.ndarray, database: dict) -> dict:
    """
    Run detection + one-face recognition. Returns JSON-serializable dict.
    `database` is the same structure as face_embeddings.pkl (name -> list of embeddings).
    """
    if frame_bgr is None:
        return {"ok": False, "error": "decode_failed", "face_count": 0}

    frame = cv2.resize(frame_bgr, (640, 480))
    faces = app.get(frame)
    n = len(faces)

    if n == 0:
        return {"ok": True, "face_count": 0, "matched": False, "user": None, "score": None, "bbox": None}

    if n > 1:
        return {
            "ok": True,
            "face_count": n,
            "matched": False,
            "user": None,
            "score": None,
            "bbox": None,
            "reason": "multiple_faces",
        }

    face = faces[0]
    emb = face.embedding.astype(np.float32)
    best_user, best_score = find_best_match(emb, database)
    matched = bool(database) and best_score >= THRESHOLD
    bbox = [float(x) for x in face.bbox.tolist()]

    return {
        "ok": True,
        "face_count": 1,
        "matched": matched,
        "user": best_user,
        "score": round(best_score, 4),
        "bbox": bbox,
    }
