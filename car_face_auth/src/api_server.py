"""Local HTTP API: browser webcam frames -> InsightFace (same DB as enroll.py)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from insightface.app import FaceAnalysis
from pydantic import BaseModel

from .face_engine import (
    SAMPLES_NEEDED,
    analyze_frame,
    decode_image_bytes,
    extract_single_face_embedding,
    load_database,
    save_database,
)

_model: dict[str, FaceAnalysis] = {}
ENROLL_SESSIONS: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    print("Loading InsightFace (buffalo_s)...")
    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320, 320))
    _model["app"] = app
    print("Model ready. API listening.")
    yield
    _model.clear()


app = FastAPI(title="FaceLock local verify API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class EnrollStartBody(BaseModel):
    name: str


class EnrollSessionBody(BaseModel):
    session_id: str


@app.get("/api/face-status")
def face_status():
    db = load_database()
    names = list(db.keys())
    return {"enrolled": names, "count": len(names)}


@app.post("/api/verify-frame")
async def verify_frame(image: UploadFile = File(...)):
    if "app" not in _model:
        raise HTTPException(status_code=503, detail="Model not loaded")
    raw = await image.read()
    frame = decode_image_bytes(raw)
    db = load_database()
    if not db:
        raise HTTPException(
            status_code=400,
            detail="No enrolled users. Enroll via Users tab or enroll.py first.",
        )
    result = analyze_frame(_model["app"], frame, db)
    return result


@app.post("/api/enroll/start")
def enroll_start(body: EnrollStartBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    sid = uuid.uuid4().hex
    ENROLL_SESSIONS[sid] = {"name": name, "embeddings": []}
    return {"session_id": sid, "samples_needed": SAMPLES_NEEDED}


@app.get("/api/enroll/status")
def enroll_status(session_id: str):
    s = ENROLL_SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")
    return {
        "name": s["name"],
        "count": len(s["embeddings"]),
        "samples_needed": SAMPLES_NEEDED,
    }


@app.post("/api/enroll/sample")
async def enroll_sample(session_id: str = Form(...), image: UploadFile = File(...)):
    if "app" not in _model:
        raise HTTPException(status_code=503, detail="Model not loaded")
    s = ENROLL_SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")
    raw = await image.read()
    frame = decode_image_bytes(raw)
    emb, n = extract_single_face_embedding(_model["app"], frame)
    if emb is None:
        if n == 0:
            return {
                "ok": False,
                "face_count": 0,
                "count": len(s["embeddings"]),
                "samples_needed": SAMPLES_NEEDED,
                "message": "no_face",
            }
        return {
            "ok": False,
            "face_count": n,
            "count": len(s["embeddings"]),
            "samples_needed": SAMPLES_NEEDED,
            "message": "multiple_faces",
        }
    s["embeddings"].append(emb)
    return {
        "ok": True,
        "face_count": 1,
        "count": len(s["embeddings"]),
        "samples_needed": SAMPLES_NEEDED,
    }


@app.post("/api/enroll/finish")
def enroll_finish(body: EnrollSessionBody):
    s = ENROLL_SESSIONS.pop(body.session_id, None)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")
    embs = s["embeddings"]
    if len(embs) < SAMPLES_NEEDED:
        raise HTTPException(
            status_code=400,
            detail=f"Need {SAMPLES_NEEDED} samples, have {len(embs)}",
        )
    db = load_database()
    db[s["name"]] = embs
    save_database(db)
    return {"ok": True, "user": s["name"], "samples": len(embs)}


@app.post("/api/enroll/cancel")
def enroll_cancel(body: EnrollSessionBody):
    ENROLL_SESSIONS.pop(body.session_id, None)
    return {"ok": True}
