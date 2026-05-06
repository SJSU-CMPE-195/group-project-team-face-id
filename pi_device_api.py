"""
HTTP API for the face-ui Device mode. Uses SQLite on the Pi via db.py / db_api.py.

Run on the Pi (after `python db.py` once to init schema):
  pip install flask
  python pi_device_api.py

Optional: FACEID_DB_PATH=/path/to/faceid.db  PORT=5000
"""
import os
import threading

from flask import Flask, jsonify, request

from db import init_db
import db_api

app = Flask(__name__)
_auto_relock_timer = None
_auto_relock_lock = threading.Lock()
_ignition_stop_timer = None
_ignition_stop_lock = threading.Lock()
_ignition_running = False
_ignition_lock = threading.Lock()


def _cancel_auto_relock_timer():
    global _auto_relock_timer
    with _auto_relock_lock:
        if _auto_relock_timer is not None:
            _auto_relock_timer.cancel()
            _auto_relock_timer = None


def _schedule_auto_relock():
    global _auto_relock_timer
    settings = db_api.get_settings_for_ui()
    secs = int(settings.get("autoRelockSeconds", 0) or 0)
    _cancel_auto_relock_timer()
    if secs <= 0:
        return

    def _run():
        global _auto_relock_timer
        with _auto_relock_lock:
            _auto_relock_timer = None
        db_api.set_lock(reason=f"auto_relock_{secs}s")
        _set_ignition(False, reason="auto_relock")

    with _auto_relock_lock:
        _auto_relock_timer = threading.Timer(secs, _run)
        _auto_relock_timer.daemon = True
        _auto_relock_timer.start()


def _cancel_ignition_stop_timer():
    global _ignition_stop_timer
    with _ignition_stop_lock:
        if _ignition_stop_timer is not None:
            _ignition_stop_timer.cancel()
            _ignition_stop_timer = None


def _schedule_ignition_stop():
    global _ignition_stop_timer
    settings = db_api.get_settings_for_ui()
    secs = int(settings.get("ignitionAutoStopSeconds", 0) or 0)
    _cancel_ignition_stop_timer()
    if secs <= 0:
        return

    def _run():
        global _ignition_stop_timer
        with _ignition_stop_lock:
            _ignition_stop_timer = None
        _set_ignition(False, reason=f"ignition_timeout_{secs}s")

    with _ignition_stop_lock:
        _ignition_stop_timer = threading.Timer(secs, _run)
        _ignition_stop_timer.daemon = True
        _ignition_stop_timer.start()


def _set_ignition(running: bool, reason: str = "manual"):
    global _ignition_running
    with _ignition_lock:
        if _ignition_running == running:
            return
        _ignition_running = running
    if running:
        _schedule_ignition_stop()
    else:
        _cancel_ignition_stop_timer()
    db_api.log_event("ignition", "ok", detail=f"{'start' if running else 'stop'}:{reason}")


@app.after_request
def cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


@app.get("/api/status")
def api_status():
    s = db_api.get_status()
    with _ignition_lock:
        s["ignitionOn"] = _ignition_running
    return jsonify(s)


@app.post("/api/unlock")
def api_unlock():
    body = request.get_json(silent=True) or {}
    reason = body.get("reason") or "manual_ui"
    db_api.set_unlock(reason=reason)
    _schedule_auto_relock()
    return jsonify({"ok": True})


@app.post("/api/lock")
def api_lock():
    body = request.get_json(silent=True) or {}
    reason = body.get("reason") or "manual_ui"
    _cancel_auto_relock_timer()
    _set_ignition(False, reason=reason)
    db_api.set_lock(reason=reason)
    return jsonify({"ok": True})


@app.post("/api/ignition/start")
def api_ignition_start():
    s = db_api.get_status()
    if s.get("lockState") == "locked":
        return jsonify({"error": "device is locked"}), 409
    _set_ignition(True, reason="api_start")
    return jsonify({"ok": True})


@app.post("/api/ignition/stop")
def api_ignition_stop():
    _set_ignition(False, reason="api_stop")
    return jsonify({"ok": True})


@app.post("/api/full-reset")
def api_full_reset():
    _cancel_auto_relock_timer()
    _cancel_ignition_stop_timer()
    _set_ignition(False, reason="full_reset")
    db_api.set_lock(reason="full_reset")
    return jsonify({"ok": True})


@app.get("/api/users")
def api_users():
    return jsonify(db_api.list_users_for_ui())


@app.post("/api/users")
def api_add_user():
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    user = db_api.add_user(name)
    return jsonify(user), 201


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
    import base64
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
    import base64
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


@app.get("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
