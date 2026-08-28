"""Canonical Flask Device API for the Raspberry Pi runtime.

The systemd service starts this file. Camera, InsightFace, and ESP32 work is
owned by :class:`car_face_auth.src.pi_runtime.PiRuntime` and happens lazily in
background sessions so this module remains importable off-Pi.
"""

from __future__ import annotations

import atexit
import os
import signal
from typing import Any

from flask import Flask, jsonify, request

from db import init_db
import db_api
from car_face_auth.src.pi_runtime import PiRuntime, RuntimeRequestError


MAX_CLIENT_IMAGE_BYTES = 8 * 1024 * 1024


def create_app(db_module: Any = db_api, runtime: PiRuntime | None = None) -> Flask:
    """Build the API, with a small injection seam for off-Pi route tests."""

    runtime_impl = runtime or PiRuntime(db_module)
    app = Flask(__name__)

    def runtime_error(exc: RuntimeRequestError):
        return jsonify({"ok": False, "error": str(exc)}), exc.status_code

    def json_error(message: str, status_code: int):
        return jsonify({"ok": False, "error": message}), status_code

    @app.errorhandler(RuntimeRequestError)
    def handle_runtime_error(exc):
        return runtime_error(exc)

    @app.errorhandler(404)
    def handle_not_found(_exc):
        return json_error("route not found", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_exc):
        return json_error("method not allowed", 405)

    def json_object() -> dict[str, Any]:
        body = request.get_json(silent=True)
        if body is None:
            if request.is_json and request.get_data(cache=True):
                raise RuntimeRequestError("valid JSON object required", 400)
            return {}
        if not isinstance(body, dict):
            raise RuntimeRequestError("JSON object required", 400)
        return body

    @app.after_request
    def cors_headers(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Cache-Control, Pragma"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
        if (
            request.path.startswith(("/api/", "/sim/"))
            or request.path in {"/health", "/ready"}
        ):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/api/<path:_any>", methods=["OPTIONS"])
    def preflight(_any):
        return "", 204

    @app.get("/api/status")
    def api_status():
        status = db_module.get_status()
        runtime_status = runtime_impl.status()
        status["ignitionOn"] = runtime_status["ignitionOn"]
        status["runtime"] = runtime_status
        return jsonify(status)

    @app.post("/api/unlock")
    def api_unlock():
        body = json_object()
        result = runtime_impl.unlock(reason=body.get("reason") or "manual_ui")
        return (jsonify(result), 200 if result.get("ok") else 503)

    @app.post("/api/lock")
    def api_lock():
        body = json_object()
        result = runtime_impl.force_lock(reason=body.get("reason") or "manual_ui")
        return (jsonify(result), 200 if result.get("ok") else 503)

    @app.post("/api/ignition/stop")
    def api_ignition_stop():
        result = runtime_impl.set_ignition(False, reason="api_stop")
        return jsonify(result), 200 if result.get("ok") else 503

    @app.post("/api/full-reset")
    def api_full_reset():
        result = runtime_impl.force_lock(reason="full_reset")
        return jsonify(result), 200 if result.get("ok") else 503

    @app.get("/api/users")
    def api_users():
        return jsonify(db_module.list_users_for_ui())

    @app.get("/api/face-status")
    def api_face_status():
        try:
            rows = db_module.get_all_face_encodings()
            names = sorted({str(row["name"]).strip() for row in rows if str(row["name"]).strip()})
        except Exception as exc:
            return json_error(f"face database unavailable: {exc}", 503)
        return jsonify({"enrolled": names, "count": len(names)})

    @app.post("/api/users")
    def api_add_user():
        body = json_object()
        name = (body.get("name") or "").strip()
        if not name:
            return json_error("name is required", 400)
        result = db_module.add_user(name)
        if result.get("ok") is False:
            status = 409 if "already exists" in result.get("error", "") else 400
            return jsonify(result), status
        return jsonify(result), 201

    @app.delete("/api/users/<user_id>")
    def api_delete_user(user_id):
        delete_user = getattr(runtime_impl, "delete_user", db_module.delete_user)
        out = delete_user(user_id)
        if not out.get("ok"):
            return json_error("user not found", 404)
        return jsonify({"ok": True})

    @app.patch("/api/users/<user_id>/access")
    def api_set_access(user_id):
        body = json_object()
        if "allowed" not in body:
            return json_error("allowed field required", 400)
        if not isinstance(body["allowed"], bool):
            return json_error("allowed must be a boolean", 400)
        set_user_access = getattr(runtime_impl, "set_user_access", db_module.set_user_access)
        result = set_user_access(user_id, body["allowed"])
        return jsonify(result), 200 if result.get("ok") else 404

    @app.post("/api/verify-log")
    def api_verify_log():
        body = json_object()
        db_module.log_event(
            stage="face_verify",
            result=body.get("result", "unknown"),
            detail=body.get("detail", ""),
            user_id=body.get("user_id"),
        )
        return jsonify({"ok": True})

    @app.get("/api/logs")
    def api_logs():
        return jsonify(db_module.list_logs_for_ui())

    @app.get("/api/settings")
    def api_get_settings():
        return jsonify(db_module.get_settings_for_ui())

    @app.post("/api/settings")
    def api_settings():
        body = json_object()
        db_module.save_settings_from_ui(body)
        return jsonify({"ok": True})

    @app.post("/api/scan/start")
    def api_scan_start():
        body = json_object()
        source = str(body.get("source") or "pi_camera").strip().lower()
        try:
            scan_args = {
                "purpose": body.get("purpose") or "unlock",
                "expected_user": body.get("expected_user") or body.get("expectedUser"),
            }
            if source == "pi_camera":
                result = runtime_impl.start_scan(**scan_args)
            elif source in {"client_camera", "device_camera", "phone_camera"}:
                result = runtime_impl.start_client_scan(**scan_args)
            else:
                raise RuntimeRequestError("source must be pi_camera or client_camera", 400)
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        return jsonify(result)

    @app.post("/api/scan/sample")
    def api_scan_sample():
        session_id = request.form.get("session_id") or request.form.get("sessionId")
        if not session_id:
            return json_error("session_id is required", 400)
        image = request.files.get("image")
        if image is None:
            return json_error("image is required", 400)
        if not (image.mimetype or "").lower().startswith("image/"):
            return json_error("image must use an image content type", 415)
        image_bytes = image.stream.read(MAX_CLIENT_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_CLIENT_IMAGE_BYTES:
            return json_error("image is too large", 413)
        try:
            return jsonify(runtime_impl.add_client_scan_sample(session_id, image_bytes))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.get("/api/scan/status")
    def api_scan_status():
        session_id = request.args.get("session_id") or request.args.get("sessionId")
        if not session_id:
            return jsonify({"ok": False, "error": "session_id is required"}), 400
        try:
            return jsonify(runtime_impl.scan_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/scan/cancel")
    def api_scan_cancel():
        body = json_object()
        session_id = body.get("session_id") or body.get("sessionId")
        if not session_id:
            return jsonify({"ok": False, "error": "session_id is required"}), 400
        try:
            return jsonify(runtime_impl.cancel_scan(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/start")
    def api_enroll_start():
        body = json_object()
        source = str(body.get("source") or "pi_camera").strip().lower()
        try:
            if source == "pi_camera":
                result = runtime_impl.start_enrollment(body.get("name") or "")
            elif source in {"client_camera", "device_camera", "phone_camera"}:
                result = runtime_impl.start_client_enrollment(body.get("name") or "")
            else:
                raise RuntimeRequestError("source must be pi_camera or client_camera", 400)
            return jsonify(result)
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/sample")
    def api_enroll_sample():
        session_id = request.form.get("session_id") or request.form.get("sessionId")
        if not session_id:
            return json_error("session_id is required", 400)
        image = request.files.get("image")
        if image is None:
            return json_error("image is required", 400)
        if not (image.mimetype or "").lower().startswith("image/"):
            return json_error("image must use an image content type", 415)
        image_bytes = image.stream.read(MAX_CLIENT_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_CLIENT_IMAGE_BYTES:
            return json_error("image is too large", 413)
        try:
            return jsonify(runtime_impl.add_client_enrollment_sample(session_id, image_bytes))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/finish")
    def api_enroll_finish():
        body = json_object()
        session_id = body.get("session_id") or body.get("sessionId")
        if not session_id:
            return json_error("session_id is required", 400)
        try:
            result = runtime_impl.finish_client_enrollment(session_id)
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        return jsonify(result), 200 if result.get("ok") else 503

    @app.get("/api/enroll/status")
    def api_enroll_status():
        session_id = request.args.get("session_id") or request.args.get("sessionId")
        if not session_id:
            return jsonify({"ok": False, "error": "session_id is required"}), 400
        try:
            return jsonify(runtime_impl.enrollment_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/cancel")
    def api_enroll_cancel():
        body = json_object()
        session_id = body.get("session_id") or body.get("sessionId")
        if not session_id:
            return jsonify({"ok": False, "error": "session_id is required"}), 400
        try:
            return jsonify(runtime_impl.cancel_enrollment(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.get("/health")
    def health():
        runtime_status = runtime_impl.status()
        return jsonify({
            "ok": True,
            "service": "pi_device_api",
            "runtime_ready": runtime_status["ready"],
            "runtime": runtime_status,
        })

    @app.get("/ready")
    def ready():
        runtime_status = runtime_impl.status()
        payload = {
            "ok": bool(runtime_status["ready"]),
            "service": "pi_device_api",
            "runtime": runtime_status,
        }
        return jsonify(payload), 200 if payload["ok"] else 503

    return app


_runtime = PiRuntime(db_api)
app = create_app(runtime=_runtime)
atexit.register(_runtime.close)


def _shutdown_handler(_signum, _frame):
    _runtime.close()
    raise SystemExit(0)


if __name__ == "__main__":
    init_db()
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    startup = _runtime.force_lock(reason="startup")
    if not startup.get("ok"):
        print(f"WARNING: startup fail-safe did not reach the ESP32: {startup.get('error')}", flush=True)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
