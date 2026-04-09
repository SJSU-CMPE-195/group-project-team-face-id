"""
HTTP API for the face-ui Device mode. Uses SQLite on the Pi via db.py / db_api.py.

Run on the Pi (after `python db.py` once to init schema):
  pip install flask
  python pi_device_api.py

Optional: FACEID_DB_PATH=/path/to/faceid.db  PORT=5000
"""
import os

from flask import Flask, jsonify, request

from db import init_db
import db_api

app = Flask(__name__)


@app.after_request
def cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
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
