"""Local HTTP API for browser-camera InsightFace development.

Receives webcam frames, runs InsightFace, and stores embeddings in the same
canonical SQLite database used by the Pi Device API.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from insightface.app import FaceAnalysis
from pydantic import BaseModel

from .face_engine import (
    SAMPLES_NEEDED,
    analyze_frame,
    decode_image_bytes,
    delete_embedding_for_name,
    extract_single_face_embedding,
    load_database,
    save_user_embedding,
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

# This service writes users.face_encoding in the same database the Pi unlocks
# from, so enrolling a face here is equivalent to cutting a key.  It is a
# development tool and a trusted internal callee -- never a public endpoint.
INTERNAL_TOKEN = (
    os.environ.get("FACEID_INTERNAL_TOKEN")
    or os.environ.get("FACEID_API_TOKEN")
    or ""
).strip()

# Previously allow_origins=["*"] together with allow_credentials=True, which
# makes Starlette echo back whatever origin asks and permits credentialed
# cross-site calls from any page.  Credentials are not used here at all, so the
# flag is gone and origins are an explicit allowlist.
_DEFAULT_DEV_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:4173,http://127.0.0.1:4173"
)
ALLOWED_ORIGINS = [
    o.strip().rstrip("/")
    for o in os.environ.get("FACEID_FACE_API_ORIGINS", _DEFAULT_DEV_ORIGINS).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Internal-Token"],
)


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.middleware("http")
async def require_internal_caller(request: Request, call_next):
    """Fail safe: only the local host, or a caller holding the service token.

    With no token configured the service still runs for local development but
    refuses anything arriving from another machine, so an accidental
    ``--host 0.0.0.0`` cannot quietly expose face enrollment to the network.
    """
    if request.method == "OPTIONS":
        return await call_next(request)

    presented = (request.headers.get("X-Internal-Token") or "").strip()
    if INTERNAL_TOKEN and presented and hmac.compare_digest(presented, INTERNAL_TOKEN):
        return await call_next(request)

    client_host = request.client.host if request.client else None
    if _is_loopback(client_host):
        return await call_next(request)

    print(f"Refused Face API request from {client_host} to {request.url.path}")
    return JSONResponse(
        {"ok": False, "detail": "this service is not reachable from the network"},
        status_code=403,
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


class FaceRemoveBody(BaseModel):
    name: str


@app.post("/api/face/remove")
async def face_remove(body: FaceRemoveBody):
    result = delete_embedding_for_name(body.name)
    return result


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
    result = save_user_embedding(s["name"], embs)
    if not result.get("ok"):
        status_code = 503 if result.get("error") == "database unavailable" else 409
        raise HTTPException(status_code=status_code, detail=result.get("error") or "Could not store embedding")
    return {"ok": True, "user": s["name"], "samples": len(embs)}


@app.post("/api/enroll/cancel")
def enroll_cancel(body: EnrollSessionBody):
    ENROLL_SESSIONS.pop(body.session_id, None)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    # Loopback by default, and binding wider takes a deliberate opt-in.  The
    # host is chosen here rather than left to whatever --host a command line
    # happens to carry.
    host = "127.0.0.1"
    if os.environ.get("FACEID_FACE_API_PUBLIC", "").strip() in ("1", "true", "yes"):
        if not INTERNAL_TOKEN:
            raise SystemExit(
                "Refusing to bind publicly without FACEID_INTERNAL_TOKEN set."
            )
        host = "0.0.0.0"
    uvicorn.run(app, host=host, port=int(os.environ.get("FACE_API_PORT", "8765")))
