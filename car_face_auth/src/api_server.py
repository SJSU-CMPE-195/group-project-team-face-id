"""Local HTTP API: browser webcam frames -> InsightFace (same DB as enroll.py)."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any

import cv2
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from insightface.app import FaceAnalysis
from pydantic import BaseModel

from .face_engine import (
    SAMPLES_NEEDED,
    analyze_frame,
    decode_image_bytes,
    extract_single_face_embedding,
    load_database,
    save_database,
    save_user_embedding,
)

try:
    from picamera2 import Picamera2
    _PI_CAMERA_AVAILABLE = True
except ImportError:
    _PI_CAMERA_AVAILABLE = False

_model: dict[str, FaceAnalysis] = {}
_picam: Any = None
ENROLL_SESSIONS: dict[str, dict[str, Any]] = {}


def _init_picam():
    cam = Picamera2()
    cam.configure(cam.create_preview_configuration(main={"format": "RGB888", "size": (640, 480)}))
    cam.start()
    return cam


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _picam
    print("Loading InsightFace (buffalo_s)...")
    fa = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    fa.prepare(ctx_id=-1, det_size=(320, 320))
    _model["app"] = fa
    if _PI_CAMERA_AVAILABLE:
        try:
            loop = asyncio.get_event_loop()
            _picam = await loop.run_in_executor(None, _init_picam)
            print("Pi camera ready.")
        except Exception as e:
            print(f"Pi camera init failed: {e}")
    print("Model ready. API listening.")
    yield
    _model.clear()
    if _picam is not None:
        try:
            _picam.stop()
        except Exception:
            pass


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
    try:
        save_user_embedding(s["name"], embs)
    except Exception:
        # SQLite unavailable — fall back to .pkl
        db = load_database()
        db[s["name"]] = embs
        save_database(db)
    return {"ok": True, "user": s["name"], "samples": len(embs)}


@app.post("/api/enroll/cancel")
def enroll_cancel(body: EnrollSessionBody):
    ENROLL_SESSIONS.pop(body.session_id, None)
    return {"ok": True}


@app.get("/api/pi-camera-available")
def pi_camera_available():
    return {"available": _PI_CAMERA_AVAILABLE and _picam is not None}


@app.get("/api/pi-stream")
async def pi_stream():
    if not _PI_CAMERA_AVAILABLE or _picam is None:
        raise HTTPException(status_code=503, detail="Pi camera not available")

    async def generate():
        loop = asyncio.get_event_loop()
        while True:
            frame = await loop.run_in_executor(None, _picam.capture_array)
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            _, jpeg = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
            await asyncio.sleep(0.033)

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/pi-verify")
async def pi_verify():
    if not _PI_CAMERA_AVAILABLE or _picam is None:
        raise HTTPException(status_code=503, detail="Pi camera not available")
    if "app" not in _model:
        raise HTTPException(status_code=503, detail="Model not loaded")
    loop = asyncio.get_event_loop()
    frame = await loop.run_in_executor(None, _picam.capture_array)
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    db = load_database()
    if not db:
        raise HTTPException(status_code=400, detail="No enrolled users.")
    return analyze_frame(_model["app"], frame_bgr, db)
