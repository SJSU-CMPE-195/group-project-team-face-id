"""
Fake Pi API for developing the remote-camera UI without Raspberry Pi hardware.

Run from the repo root:
  python mock_pi_device_api.py

Then set the dashboard Device API Base URL to:
  http://127.0.0.1:5055
"""

import os
import base64
import pickle
import sys
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("FACEID_DB_PATH", str(REPO_ROOT / ".cache" / "mock_faceid.db"))

from db import init_db
import db_api

app = Flask(__name__)

SCAN_SESSIONS = {}
ENROLL_SESSIONS = {}
SESSIONS_LOCK = threading.Lock()
IGNITION_RUNNING = False
IGNITION_LOCK = threading.Lock()

WINDOW_SIZE = 10
MIN_MATCHES = 6
SAMPLES_NEEDED = 10


def _now_ms():
    return int(time.time() * 1000)


def _session_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return resp


app.after_request(_cors)


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


def _ensure_mock_user():
    users = db_api.list_users_for_ui()
    if users:
        return users[0]
    return db_api.add_user("Demo Driver")


def _ensure_user_by_name(name):
    display_name = (name or "").strip() or "Demo Driver"
    for user in db_api.list_users_for_ui():
        if user["name"] == display_name:
            return user
    return db_api.add_user(display_name)


def _set_ignition(running, reason="mock"):
    global IGNITION_RUNNING
    with IGNITION_LOCK:
        IGNITION_RUNNING = bool(running)
    db_api.log_event("ignition", "ok", detail=f"{'start' if running else 'stop'}:{reason}")


def _status_with_ignition():
    status = db_api.get_status()
    with IGNITION_LOCK:
        status["ignitionOn"] = IGNITION_RUNNING
    return status


def _scan_view(session):
    return {
        "ok": session.get("state") != "error",
        "session_id": session["id"],
        "purpose": session["purpose"],
        "state": session["state"],
        "user": session.get("user"),
        "score": session.get("score"),
        "face_count": session.get("face_count", 1),
        "message": session.get("message", ""),
        "window": {
            "matches": session.get("matches", 0),
            "needed": MIN_MATCHES,
            "size": WINDOW_SIZE,
        },
        "updated_at": session.get("updated_at", _now_ms()),
    }


def _enroll_view(session):
    return {
        "ok": session.get("state") != "error",
        "session_id": session["id"],
        "state": session["state"],
        "source": "pi_camera",
        "user": session["name"],
        "count": session.get("count", 0),
        "samples_needed": SAMPLES_NEEDED,
        "message": session.get("message", ""),
        "updated_at": session.get("updated_at", _now_ms()),
    }


def _run_fake_scan(session_id):
    with SESSIONS_LOCK:
        session = SCAN_SESSIONS.get(session_id)
    if not session:
        return

    fake_result = session.get("fake_result", "granted")
    purpose = session["purpose"]
    expected_user = session.get("expected_user")
    user = _ensure_mock_user()
    user_name = expected_user or user["name"]

    for matches in range(1, MIN_MATCHES + 1):
        time.sleep(0.55)
        with SESSIONS_LOCK:
            session = SCAN_SESSIONS.get(session_id)
            if not session or session["state"] == "cancelled":
                return
            session.update(
                state="scanning",
                user=user_name,
                score=round(0.56 + matches * 0.035, 3),
                face_count=1,
                matches=matches,
                message=f"Fake Pi camera matched {matches}/{WINDOW_SIZE}.",
                updated_at=_now_ms(),
            )

    status = _status_with_ignition()
    if fake_result != "granted":
        final_state = "denied"
        message = "Fake scan denied by requested result."
    elif purpose == "ignition" and status.get("lockState") == "locked":
        final_state = "denied"
        message = "Ignition scan denied because the device is locked."
    else:
        final_state = "granted"
        message = "Fake Pi scan granted."
        if purpose == "ignition":
            _set_ignition(True, reason=f"scan:{session_id}")
        else:
            db_api.set_unlock(reason=f"scan:{session_id}")

    with SESSIONS_LOCK:
        session = SCAN_SESSIONS.get(session_id)
        if not session or session["state"] == "cancelled":
            return
        session.update(
            state=final_state,
            user=user_name,
            score=0.81,
            face_count=1,
            matches=MIN_MATCHES if final_state == "granted" else max(0, MIN_MATCHES - 2),
            message=message,
            updated_at=_now_ms(),
        )
    db_api.log_event("face_scan", "ok" if final_state == "granted" else "fail", detail=message)


def _run_fake_enroll(session_id):
    for count in range(1, SAMPLES_NEEDED + 1):
        time.sleep(0.45)
        with SESSIONS_LOCK:
            session = ENROLL_SESSIONS.get(session_id)
            if not session or session["state"] == "cancelled":
                return
            session.update(
                state="capturing",
                count=count,
                message=f"Fake Pi camera captured {count}/{SAMPLES_NEEDED}.",
                updated_at=_now_ms(),
            )

    with SESSIONS_LOCK:
        session = ENROLL_SESSIONS.get(session_id)
        if not session or session["state"] == "cancelled":
            return
        name = session["name"]

    user = _ensure_user_by_name(name)
    try:
        db_api.set_user_embedding(user["id"], pickle.dumps([]))
    except Exception:
        db_api.log_event("enroll_embedding", "ok", detail=f"Mock embedding stored for {name}", user_id=user["id"])

    with SESSIONS_LOCK:
        session = ENROLL_SESSIONS.get(session_id)
        if session:
            session.update(
                state="completed",
                count=SAMPLES_NEEDED,
                message="Fake Pi camera enrollment completed.",
                updated_at=_now_ms(),
            )


@app.get("/api/status")
def api_status():
    return jsonify(_status_with_ignition())


@app.post("/api/unlock")
def api_unlock():
    body = request.get_json(silent=True) or {}
    db_api.set_unlock(reason=body.get("reason") or "manual_ui")
    return jsonify({"ok": True})


@app.post("/api/lock")
def api_lock():
    body = request.get_json(silent=True) or {}
    _set_ignition(False, reason=body.get("reason") or "manual_ui")
    db_api.set_lock(reason=body.get("reason") or "manual_ui")
    return jsonify({"ok": True})


@app.post("/api/ignition/start")
def api_ignition_start():
    if _status_with_ignition().get("lockState") == "locked":
        return jsonify({"error": "device is locked"}), 409
    _set_ignition(True, reason="manual_ui")
    return jsonify({"ok": True})


@app.post("/api/ignition/stop")
def api_ignition_stop():
    _set_ignition(False, reason="manual_ui")
    return jsonify({"ok": True})


@app.post("/api/full-reset")
def api_full_reset():
    _set_ignition(False, reason="full_reset")
    db_api.set_lock(reason="full_reset")
    return jsonify({"ok": True})


@app.get("/api/users")
def api_users():
    _ensure_mock_user()
    return jsonify(db_api.list_users_for_ui())


@app.post("/api/users")
def api_add_user():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    return jsonify(_ensure_user_by_name(name)), 201


@app.delete("/api/users/<user_id>")
def api_delete_user(user_id):
    out = db_api.delete_user(user_id)
    if not out.get("ok"):
        return jsonify({"error": "user not found"}), 404
    return jsonify({"ok": True})


@app.patch("/api/users/<user_id>/access")
def api_set_access(user_id):
    body = request.get_json(silent=True) or {}
    if "allowed" not in body:
        return jsonify({"error": "allowed field required"}), 400
    return jsonify(db_api.set_user_access(user_id, bool(body["allowed"])))


@app.patch("/api/users/<user_id>/embedding")
def api_set_embedding(user_id):
    body = request.get_json(silent=True) or {}
    blob_b64 = body.get("blob", "")
    if not blob_b64:
        return jsonify({"error": "blob field required"}), 400
    try:
        blob = base64.b64decode(blob_b64)
    except Exception:
        return jsonify({"error": "invalid base64"}), 400
    return jsonify(db_api.set_user_embedding(user_id, blob))


@app.get("/api/users/<user_id>/embedding")
def api_get_embedding(user_id):
    row = db_api.get_user_by_id(user_id)
    if not row or not row.get("face_encoding"):
        return jsonify({"error": "no embedding"}), 404
    return jsonify({"blob": base64.b64encode(row["face_encoding"]).decode()})


@app.post("/api/verify-log")
def api_verify_log():
    body = request.get_json(silent=True) or {}
    db_api.log_event(
        stage="face_verify",
        result=body.get("result", "unknown"),
        detail=body.get("detail", ""),
        user_id=body.get("user_id"),
    )
    return jsonify({"ok": True})


@app.get("/api/logs")
def api_logs():
    return jsonify(db_api.list_logs_for_ui())


@app.get("/api/settings")
def api_get_settings():
    return jsonify(db_api.get_settings_for_ui())


@app.post("/api/settings")
def api_settings():
    body = request.get_json(silent=True) or {}
    db_api.save_settings_from_ui(body)
    return jsonify({"ok": True})


@app.post("/api/scan/start")
def api_scan_start():
    body = request.get_json(silent=True) or {}
    purpose = (body.get("purpose") or "unlock").strip()
    if purpose not in ("unlock", "ignition"):
        return jsonify({"error": "purpose must be unlock or ignition"}), 400
    session_id = _session_id("scan")
    session = {
        "id": session_id,
        "purpose": purpose,
        "expected_user": body.get("expected_user") or body.get("expectedUser"),
        "fake_result": body.get("fake_result") or body.get("fakeResult") or "granted",
        "state": "scanning",
        "matches": 0,
        "message": "Fake Pi camera scan started.",
        "updated_at": _now_ms(),
    }
    with SESSIONS_LOCK:
        SCAN_SESSIONS[session_id] = session
    thread = threading.Thread(target=_run_fake_scan, args=(session_id,), daemon=True)
    thread.start()
    return jsonify(_scan_view(session))


@app.get("/api/scan/status")
def api_scan_status():
    session_id = request.args.get("session_id", "")
    with SESSIONS_LOCK:
        session = SCAN_SESSIONS.get(session_id)
        if not session:
            return jsonify({"error": "unknown scan session"}), 404
        return jsonify(_scan_view(session))


@app.post("/api/scan/cancel")
def api_scan_cancel():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id") or body.get("sessionId")
    with SESSIONS_LOCK:
        session = SCAN_SESSIONS.get(session_id)
        if not session:
            return jsonify({"error": "unknown scan session"}), 404
        session.update(state="cancelled", message="Scan cancelled.", updated_at=_now_ms())
        return jsonify(_scan_view(session))


@app.post("/api/enroll/start")
def api_enroll_start():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    session_id = _session_id("enroll")
    session = {
        "id": session_id,
        "name": name,
        "state": "capturing",
        "count": 0,
        "message": "Fake Pi camera enrollment started.",
        "updated_at": _now_ms(),
    }
    with SESSIONS_LOCK:
        ENROLL_SESSIONS[session_id] = session
    thread = threading.Thread(target=_run_fake_enroll, args=(session_id,), daemon=True)
    thread.start()
    return jsonify(_enroll_view(session))


@app.get("/api/enroll/status")
def api_enroll_status():
    session_id = request.args.get("session_id", "")
    with SESSIONS_LOCK:
        session = ENROLL_SESSIONS.get(session_id)
        if not session:
            return jsonify({"error": "unknown enrollment session"}), 404
        return jsonify(_enroll_view(session))


@app.post("/api/enroll/cancel")
def api_enroll_cancel():
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id") or body.get("sessionId")
    with SESSIONS_LOCK:
        session = ENROLL_SESSIONS.get(session_id)
        if not session:
            return jsonify({"error": "unknown enrollment session"}), 404
        session.update(state="cancelled", message="Enrollment cancelled.", updated_at=_now_ms())
        return jsonify(_enroll_view(session))


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "mock_pi_device_api"})


if __name__ == "__main__":
    init_db()
    _ensure_mock_user()
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=False)
