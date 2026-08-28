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
EMBEDDING_DIMENSION = 512
THRESHOLD = 0.75
WINDOW_SIZE = 10
MIN_MATCHES = 6


def _validate_embedding_collection(embeddings) -> list[np.ndarray]:
    """Return a safe float32 collection or raise for malformed data."""
    if not isinstance(embeddings, (list, tuple)) or not embeddings:
        raise ValueError("embedding collection must be a non-empty list")
    if len(embeddings) > SAMPLES_NEEDED:
        raise ValueError(f"embedding collection must contain at most {SAMPLES_NEEDED} samples")

    validated = []
    shape = None
    for embedding in embeddings:
        try:
            array = np.asarray(embedding, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise ValueError("embedding must contain numeric values") from exc
        if array.ndim != 1 or array.shape != (EMBEDDING_DIMENSION,) or not np.isfinite(array).all():
            raise ValueError(f"embedding must be a finite {EMBEDDING_DIMENSION}-value vector")
        if not np.any(array):
            raise ValueError("embedding vector must not be all zero")
        if shape is None:
            shape = array.shape
        elif array.shape != shape:
            raise ValueError("embedding vectors must have the same shape")
        validated.append(np.ascontiguousarray(array, dtype=np.float32))
    return validated


def _validate_database(database: dict, *, strict: bool) -> dict:
    if not isinstance(database, dict):
        if strict:
            raise ValueError("embedding database must be a mapping")
        return {}

    validated = {}
    seen_names = set()
    for raw_name, embeddings in database.items():
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        key = name.casefold()
        if not name or key in seen_names:
            if strict:
                raise ValueError("embedding database contains an empty or duplicate name")
            continue
        try:
            validated[name] = _validate_embedding_collection(embeddings)
        except ValueError:
            if strict:
                raise
            continue
        seen_names.add(key)
    return validated


def load_database() -> dict:
    """Load embeddings from SQLite (preferred) with .pkl fallback."""
    try:
        import db_api
    except ImportError:
        db_api = None

    if db_api is not None:
        try:
            rows = db_api.get_all_face_encodings()
        except Exception:
            # Authentication must fail closed when the canonical store cannot
            # be read; never resurrect a stale legacy pickle on DB failure.
            return {}
        db = {}
        for row in rows or []:
            try:
                blob = row.get("face_encoding") if isinstance(row, dict) else row["face_encoding"]
                if not blob:
                    continue
                # Kept for compatibility with existing Pi data; see the
                # migration limitation noted by the caller before deployment.
                collection = pickle.loads(blob)
                name = row.get("name") if isinstance(row, dict) else row["name"]
                validated = _validate_embedding_collection(collection)
                if isinstance(name, str) and name.strip():
                    db[name.strip()] = validated
            except Exception:
                # A bad row must not make a stale legacy file authoritative.
                continue
        return _validate_database(db, strict=False)

    # fallback to .pkl
    if EMBEDDINGS_FILE.exists():
        try:
            with open(EMBEDDINGS_FILE, "rb") as f:
                return _validate_database(pickle.load(f), strict=False)
        except Exception:
            return {}
    return {}


def save_database(db: dict) -> None:
    """Save embeddings to .pkl (legacy / standalone path)."""
    db = _validate_database(db, strict=True)
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(EMBEDDINGS_FILE, "wb") as f:
        pickle.dump(db, f)


def delete_embedding_for_name(name: str) -> dict:
    """
    Remove stored face embeddings for a display name from SQLite (clear blob) and/or pickle.
    Safe to call if the user row was already removed — still cleans pickle / orphaned blobs.
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "detail": "empty name"}
    sqlite_touched = False
    try:
        import db_api

        with db_api.get_conn() as conn:
            cur = conn.execute(
                "UPDATE users SET face_encoding=NULL WHERE name=? AND active=1",
                (name,),
            )
            sqlite_touched = cur.rowcount > 0
    except Exception:
        pass
    pkl_touched = False
    if EMBEDDINGS_FILE.exists():
        try:
            with open(EMBEDDINGS_FILE, "rb") as f:
                db = pickle.load(f)
            if isinstance(db, dict) and name in db:
                del db[name]
                save_database(db)
                pkl_touched = True
        except Exception:
            pass
    return {"ok": True, "sqlite": sqlite_touched, "pkl": pkl_touched}


def save_user_embedding(name: str, embeddings: list) -> dict:
    """Persist a list of np.ndarray embeddings for a named user into SQLite."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        collection = _validate_embedding_collection(embeddings)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    import db_api

    try:
        blob = pickle.dumps(collection)
        with db_api.get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM users WHERE active=1 "
                "AND lower(trim(name))=lower(trim(?))",
                (name,),
            ).fetchall()
            if not rows:
                return {"ok": False, "error": "user not found"}
            if len(rows) != 1:
                return {"ok": False, "error": "active user name is ambiguous"}
            cur = conn.execute(
                "UPDATE users SET face_encoding=? WHERE id=? AND active=1",
                (blob, rows[0]["id"]),
            )
            if cur.rowcount != 1:
                return {"ok": False, "error": "user not found"}
    except Exception:
        return {"ok": False, "error": "database unavailable"}

    db_api.log_event("enroll_embedding", "ok", detail=f"Embedding stored for {name}", user_id=rows[0]["id"])
    return {"ok": True}


def cosine_similarity(vector_a: np.ndarray, vector_b: np.ndarray) -> float:
    if vector_a.shape != vector_b.shape or vector_a.ndim != 1:
        return -1.0
    if not np.isfinite(vector_a).all() or not np.isfinite(vector_b).all():
        return -1.0
    if not np.any(vector_a) or not np.any(vector_b):
        return -1.0
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
