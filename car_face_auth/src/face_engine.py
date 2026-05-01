"""Shared InsightFace helpers for the HTTP verify API."""

from __future__ import annotations

import os
import pickle
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

_DB_PATH = Path(os.environ.get("FACEID_DB_PATH", "/home/pi/faceid/faceid.db"))
EMBEDDINGS_FILE = _DB_PATH.parent / "face_embeddings.pkl"  # backup

SAMPLES_NEEDED = 10
THRESHOLD = 0.45
WINDOW_SIZE = 10
MIN_MATCHES = 6


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS face_embeddings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT    NOT NULL,
            embedding BLOB    NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s', 'now'))
        )
        """
    )
    conn.commit()
    return conn


def load_database() -> dict:
    # Migrate from pickle if table is empty and backup exists
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM face_embeddings").fetchone()[0]
    finally:
        conn.close()
    if count == 0 and EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            legacy = pickle.load(f)
        if legacy:
            save_database(legacy)
            return legacy

    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT name, embedding FROM face_embeddings ORDER BY id"
        ).fetchall()
    finally:
        conn.close()

    db: dict = {}
    for name, blob in rows:
        emb = np.frombuffer(blob, dtype=np.float32).copy()
        db.setdefault(name, []).append(emb)
    return db


def save_database(db: dict) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM face_embeddings")
        for name, embeddings in db.items():
            for emb in embeddings:
                conn.execute(
                    "INSERT INTO face_embeddings (name, embedding) VALUES (?, ?)",
                    (name, emb.astype(np.float32).tobytes()),
                )
        conn.commit()
    finally:
        conn.close()

    # Write pickle backup
    with open(EMBEDDINGS_FILE, "wb") as f:
        pickle.dump(db, f)


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
    `database` maps name -> list of embeddings (same structure returned by load_database).
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
