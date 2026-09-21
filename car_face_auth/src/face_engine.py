"""Shared InsightFace helpers for the HTTP verify API (same DB as enroll.py)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from .embedding_format import (
    EMBEDDING_DIMENSION,
    MAX_EMBEDDINGS,
    EmbeddingFormatError,
    TemplateMigrationRequired,
    decode_embeddings,
    encode_embeddings,
)

CAR_FACE_AUTH_ROOT = Path(__file__).resolve().parent.parent
# Make db_api importable when running from the car_face_auth directory
_REPO_ROOT = CAR_FACE_AUTH_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOGGER = logging.getLogger(__name__)

SAMPLES_NEEDED = MAX_EMBEDDINGS
THRESHOLD = 0.75
WINDOW_SIZE = 10
MIN_MATCHES = 6


def load_database() -> dict:
    """Load safe-format embeddings from the canonical SQLite store."""
    try:
        import db_api
    except ImportError:
        LOGGER.error("Unable to load the canonical face template store.")
        return {}

    try:
        rows = db_api.get_all_face_encodings()
    except Exception:
        LOGGER.error("Unable to read face templates; authentication is disabled.")
        return {}

    database = {}
    seen_names = set()
    for row in rows or []:
        blob = row.get("face_encoding") if isinstance(row, dict) else row["face_encoding"]
        name = row.get("name") if isinstance(row, dict) else row["name"]
        clean_name = name.strip() if isinstance(name, str) else ""
        name_key = clean_name.casefold()
        if not clean_name or name_key in seen_names or not blob:
            LOGGER.warning("Skipped a face template with invalid account metadata.")
            continue
        try:
            database[clean_name] = decode_embeddings(blob)
        except TemplateMigrationRequired:
            LOGGER.warning(
                "Skipped a legacy face template; offline migration is required."
            )
            continue
        except EmbeddingFormatError:
            LOGGER.warning("Skipped an invalid face template.")
            continue
        seen_names.add(name_key)
    return database


def delete_embedding_for_name(name: str) -> dict:
    """Clear one active user's canonical SQLite face template."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "detail": "empty name"}

    try:
        import db_api

        with db_api.get_conn() as conn:
            rows = conn.execute(
                "SELECT id FROM users WHERE active=1 "
                "AND lower(trim(name))=lower(trim(?))",
                (name,),
            ).fetchall()
            if not rows:
                return {"ok": False, "detail": "user not found"}
            if len(rows) != 1:
                return {"ok": False, "detail": "active user name is ambiguous"}
            cur = conn.execute(
                "UPDATE users SET face_encoding=NULL WHERE id=? AND active=1",
                (rows[0]["id"],),
            )
            if cur.rowcount != 1:
                return {"ok": False, "detail": "user not found"}
            db_api._insert_log(
                conn,
                "delete_embedding",
                "ok",
                detail="Face template removed",
                user_id=rows[0]["id"],
            )
    except Exception:
        return {"ok": False, "detail": "database unavailable"}
    return {"ok": True, "sqlite": True}


def save_user_embedding(name: str, embeddings: list) -> dict:
    """Persist a list of np.ndarray embeddings for a named user into SQLite."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "name is required"}
    try:
        blob = encode_embeddings(embeddings)
    except EmbeddingFormatError as exc:
        return {"ok": False, "error": str(exc)}

    import db_api

    try:
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
            db_api._insert_log(
                conn,
                "enroll_embedding",
                "ok",
                detail="Face template stored",
                user_id=rows[0]["id"],
            )
    except Exception:
        return {"ok": False, "error": "database unavailable"}

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
    `database` maps an account name to its validated embedding samples.
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
