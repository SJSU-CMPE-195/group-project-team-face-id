"""
HTTP API for the face-ui Device mode. Uses SQLite on the Pi via db.py / db_api.py.

Run on the Pi (after `python db.py` once to init schema):
  pip install flask
  python car_face_auth/pi_device_api.py

Optional: FACEID_DB_PATH=/path/to/faceid.db  PORT=5000
"""
import os
import sys
import time
import threading
from pathlib import Path

# db.py and db_api.py live in the repo root
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import serial
import serial.tools.list_ports
from flask import Flask, jsonify, request

from db import init_db
import db_api

app = Flask(__name__)

# ── ESP32 serial connection ───────────────────────────────────────────────────

_ser = None
_ser_lock = threading.Lock()
_ESP_KEYWORDS = ["CP210", "CH340", "CH341", "FTDI", "USB-SERIAL", "USB Serial", "Silicon Labs"]


def _find_esp_port():
    for port in serial.tools.list_ports.comports():
        desc = (port.description or "") + (port.manufacturer or "")
        if any(kw.lower() in desc.lower() for kw in _ESP_KEYWORDS):
            return port.device
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None


def _connect_esp():
    global _ser
    port = _find_esp_port()
    if port is None:
        print("ESP32 not found — hardware commands disabled.")
        return
    try:
        _ser = serial.Serial(port, 115200, timeout=2)
        time.sleep(2)
        _ser.reset_input_buffer()
        print(f"ESP32 connected on {port}")
    except serial.SerialException as e:
        print(f"ESP32 connect failed: {e}")


def send_esp(cmd: str):
    with _ser_lock:
        if _ser is None:
            return
        try:
            _ser.write((cmd + "\n").encode())
        except serial.SerialException as e:
            print(f"ESP32 serial error ({cmd}): {e}")


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
    return jsonify(db_api.get_status())


@app.post("/api/unlock")
def api_unlock():
    body = request.get_json(silent=True) or {}
    reason = body.get("reason") or "manual_ui"
    db_api.set_unlock(reason=reason)
    send_esp("UNLOCK")
    return jsonify({"ok": True})


@app.post("/api/lock")
def api_lock():
    body = request.get_json(silent=True) or {}
    reason = body.get("reason") or "manual_ui"
    db_api.set_lock(reason=reason)
    send_esp("LOCK")
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
    _connect_esp()
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
