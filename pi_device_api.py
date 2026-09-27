"""Canonical Flask route factory for the authenticated BASS host.

``bass_wireless.py`` supplies the runtime and wraps this app with device
authentication before serving it. This module is not a standalone listener.
"""

from __future__ import annotations

import json
import sqlite3
from io import BytesIO
from typing import Any

from flask import Flask, g, jsonify, request
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from car_face_auth.src.pi_runtime import RuntimeRequestError
from safety_policy import BOOLEAN_SETTINGS, INTEGER_SETTING_RANGES


MAX_CLIENT_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 9 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
MAX_NAME_CHARS = 100
MAX_IDENTIFIER_CHARS = 128
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 4 * 1024 * 1024


def json_object() -> dict[str, Any]:
    """Read bounded JSON once so authorization and handlers share one input."""
    request.max_content_length = MAX_JSON_BYTES
    raw = request.get_data(cache=True)
    if not raw:
        return {}
    if not request.is_json:
        raise RuntimeRequestError("application/json content type required", 415)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise RuntimeRequestError("valid JSON object required", 400) from exc
    if not isinstance(body, dict):
        raise RuntimeRequestError("JSON object required", 400)
    return body


def _bounded_string(
    value: Any,
    field: str,
    max_chars: int,
    *,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            raise RuntimeRequestError(f"{field} is required", 400)
        return None
    if not isinstance(value, str):
        raise RuntimeRequestError(f"{field} must be a string", 400)
    value = value.strip()
    if not value:
        if required:
            raise RuntimeRequestError(f"{field} is required", 400)
        return None
    if len(value) > max_chars:
        raise RuntimeRequestError(
            f"{field} must be at most {max_chars} characters",
            400,
        )
    if not value.isprintable():
        raise RuntimeRequestError(f"{field} contains unsupported characters", 400)
    return value


def _choice(
    value: Any,
    field: str,
    choices: set[str],
    *,
    default: str,
) -> str:
    if value is None:
        return default
    parsed = _bounded_string(value, field, 32, required=True)
    assert parsed is not None
    parsed = parsed.lower()
    if parsed not in choices:
        allowed = " or ".join(sorted(choices))
        raise RuntimeRequestError(f"{field} must be {allowed}", 400)
    return parsed


def _validated_settings(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        raise RuntimeRequestError("at least one setting is required", 400)
    allowed_keys = set(INTEGER_SETTING_RANGES) | BOOLEAN_SETTINGS
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise RuntimeRequestError(
            f"unknown setting: {unknown_keys[0]}",
            400,
        )

    validated: dict[str, Any] = {}
    for key, value in payload.items():
        if key in BOOLEAN_SETTINGS:
            if type(value) is not bool:
                raise RuntimeRequestError(f"{key} must be a boolean", 400)
            validated[key] = value
            continue

        minimum, maximum = INTEGER_SETTING_RANGES[key]
        if type(value) is not int:
            raise RuntimeRequestError(f"{key} must be an integer", 400)
        if not minimum <= value <= maximum:
            raise RuntimeRequestError(
                f"{key} must be between {minimum} and {maximum}",
                400,
            )
        validated[key] = value
    return validated


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        raise RuntimeRequestError("image must be a valid JPEG", 400)
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "JPEG":
                raise RuntimeRequestError("image must be a valid JPEG", 400)
            width, height = image.size
            if width <= 0 or height <= 0:
                raise RuntimeRequestError("image has invalid dimensions", 400)
            if (
                width > MAX_IMAGE_DIMENSION
                or height > MAX_IMAGE_DIMENSION
                or width * height > MAX_IMAGE_PIXELS
            ):
                raise RuntimeRequestError("image dimensions are too large", 413)
            image.verify()
    except RuntimeRequestError:
        raise
    except Image.DecompressionBombError as exc:
        raise RuntimeRequestError("image dimensions are too large", 413) from exc
    except (OSError, UnidentifiedImageError) as exc:
        raise RuntimeRequestError("image must be a valid JPEG", 400) from exc

    return width, height


def create_app(*, db_module: Any, runtime: Any) -> Flask:
    """Build the API, with a small injection seam for off-Pi route tests."""

    runtime_impl = runtime
    app = Flask(__name__)
    app.extensions["bass_runtime"] = runtime_impl
    app.extensions["bass_db"] = db_module
    app.config.update(
        MAX_CONTENT_LENGTH=MAX_REQUEST_BYTES,
        MAX_FORM_MEMORY_SIZE=64 * 1024,
        MAX_FORM_PARTS=8,
    )

    def runtime_error(exc: RuntimeRequestError):
        return jsonify({"ok": False, "error": str(exc)}), exc.status_code

    def json_error(message: str, status_code: int):
        return jsonify({"ok": False, "error": message}), status_code

    @app.errorhandler(RuntimeRequestError)
    def handle_runtime_error(exc):
        return runtime_error(exc)

    @app.errorhandler(sqlite3.IntegrityError)
    def handle_integrity_error(_exc):
        return json_error("The change conflicts with protected user data.", 409)

    @app.errorhandler(404)
    def handle_not_found(_exc):
        return json_error("route not found", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_exc):
        return json_error("method not allowed", 405)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(_exc):
        return json_error("request body is too large", 413)

    @app.before_request
    def reject_oversized_declared_body():
        if (
            request.content_length is not None
            and request.content_length > MAX_REQUEST_BYTES
        ):
            return json_error("request body is too large", 413)
        return None

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

    @app.get("/api/camera/status")
    def api_camera_status():
        authorization = {"authorization": g.principal} if g.get("principal") else {}
        return jsonify(runtime_impl.camera_status(**authorization))

    @app.get("/api/camera/frame")
    def api_camera_frame():
        session_id = _bounded_string(
            request.args.get("session_id"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        authorization = {"authorization": g.principal} if g.get("principal") else {}
        frame = runtime_impl.camera_frame(session_id, **authorization)
        if frame is None:
            return "", 204
        return app.response_class(frame, mimetype="image/jpeg")

    @app.get("/api/camera/stream")
    def api_camera_stream():
        session_id = _bounded_string(
            request.args.get("session_id"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        authorization = {"authorization": g.principal} if g.get("principal") else {}
        stream = runtime_impl.camera_stream(session_id, **authorization)
        if stream is None:
            return "", 204
        response = app.response_class(
            stream,
            content_type="multipart/x-mixed-replace; boundary=frame",
            direct_passthrough=True,
        )
        response.headers["X-Accel-Buffering"] = "no"
        return response
    @app.post("/api/unlock")
    def api_unlock():
        body = json_object()
        expected_user = _bounded_string(
            body.get("expected_user", body.get("expectedUser")),
            "expected_user",
            MAX_NAME_CHARS,
        )
        result = runtime_impl.start_scan(
            purpose="unlock",
            expected_user=expected_user,
        )
        return jsonify(result), 202

    @app.post("/api/lock")
    def api_lock():
        body = json_object()
        reason = _bounded_string(body.get("reason"), "reason", 64) or "manual_ui"
        result = runtime_impl.force_lock(reason=reason)
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
        users = db_module.list_users_for_ui()
        commissioning = app.extensions.get("bass_commissioning")
        owner_id = commissioning.status().get("owner_user_id") if commissioning else None
        users = [{**user, "is_owner": user["id"] == owner_id} for user in users]
        actor = g.get("principal")
        if actor and not actor.is_admin:
            users = [user for user in users if user["id"] == actor.user_id]
        security = app.extensions.get("bass_security")
        if security:
            for user in users:
                identity = security.get_user(user["id"])
                user["is_admin"] = bool(identity and identity["is_admin"])
        return jsonify(users)

    @app.get("/api/face-status")
    def api_face_status():
        try:
            rows = db_module.get_all_face_encodings()
            actor = g.get("principal")
            if actor and not actor.is_admin:
                rows = [row for row in rows if row["id"] == actor.user_id]
            names = sorted({str(row["name"]).strip() for row in rows if str(row["name"]).strip()})
        except Exception as exc:
            return json_error(f"face database unavailable: {exc}", 503)
        return jsonify({"enrolled": names, "count": len(names)})

    @app.post("/api/users")
    def api_add_user():
        body = json_object()
        name = _bounded_string(
            body.get("name"),
            "name",
            MAX_NAME_CHARS,
            required=True,
        )
        security = app.extensions.get("bass_security")
        if security:
            result = security.create_user(g.principal, name, body.get("pin"),
                                          body.get("is_admin", False),
                                          body.get("enroll_face", False))
        else:
            result = db_module.add_user(name)
        if result.get("ok") is False:
            status = 409 if "already exists" in result.get("error", "") else 400
            return jsonify(result), status
        return jsonify(result), 201

    @app.delete("/api/users/<user_id>")
    def api_delete_user(user_id):
        user_id = _bounded_string(
            user_id,
            "user_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        delete_user = getattr(runtime_impl, "delete_user", db_module.delete_user)
        out = delete_user(user_id)
        if not out.get("ok"):
            return json_error("user not found", 404)
        return jsonify({"ok": True})

    @app.patch("/api/users/<user_id>/access")
    def api_set_access(user_id):
        user_id = _bounded_string(
            user_id,
            "user_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
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
        return json_error("client-authored verification logs are retired", 410)

    @app.get("/api/logs")
    def api_logs():
        return jsonify(db_module.list_logs_for_ui())

    @app.get("/api/settings")
    def api_get_settings():
        try:
            settings = db_module.get_settings_for_ui()
        except ValueError:
            return json_error("Safety settings are unavailable; repair the host configuration.", 503)
        return jsonify(settings)

    @app.post("/api/settings")
    def api_settings():
        body = _validated_settings(json_object())
        db_module.save_settings_from_ui(body)
        return jsonify({"ok": True})

    @app.post("/api/scan/start")
    def api_scan_start():
        body = json_object()
        source = _bounded_string(body.get("source"), "source", 32)
        source = source.lower() if source else "device_camera"
        purpose = _choice(
            body.get("purpose"),
            "purpose",
            {"unlock", "ignition"},
            default="unlock",
        )
        expected_user = _bounded_string(
            body.get("expected_user", body.get("expectedUser")),
            "expected_user",
            MAX_NAME_CHARS,
        )
        try:
            scan_args = {
                "purpose": purpose,
                "expected_user": expected_user,
            }
            if g.get("principal"):
                scan_args["authorization"] = g.principal
                scan_args["expected_user"] = g.principal.name
            if source in {"device_camera", "pi_camera", "pc_webcam"}:
                result = runtime_impl.start_scan(**scan_args)
            else:
                raise RuntimeRequestError(
                    "unlock and ignition verification require the backend host camera",
                    409,
                )
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        return jsonify(result)

    @app.post("/api/scan/sample")
    def api_scan_sample():
        return json_error(
            "verification frames must come from the backend host camera",
            409,
        )

    @app.get("/api/scan/status")
    def api_scan_status():
        session_id = _bounded_string(
            request.args.get("session_id") or request.args.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        try:
            return jsonify(runtime_impl.scan_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/scan/cancel")
    def api_scan_cancel():
        body = json_object()
        session_id = _bounded_string(
            body.get("session_id") or body.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        try:
            return jsonify(runtime_impl.cancel_scan(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/start")
    def api_enroll_start():
        body = json_object()
        source = _choice(
            body.get("source"),
            "source",
            {
                "client_camera",
                "device_camera",
                "pc_webcam",
                "phone_camera",
                "pi_camera",
            },
            default="device_camera",
        )
        name = _bounded_string(
            g.enrollment_user["name"] if g.get("enrollment_user") else body.get("name"),
            "name",
            MAX_NAME_CHARS,
            required=True,
        )
        try:
            authorization = {"authorization": g.principal} if g.get("principal") else {}
            if source in {"device_camera", "pi_camera", "pc_webcam"}:
                result = runtime_impl.start_enrollment(name, **authorization)
            elif source in {"client_camera", "phone_camera"}:
                result = runtime_impl.start_client_enrollment(name, **authorization)
            else:
                raise RuntimeRequestError(
                    "source must be device_camera or client_camera",
                    400,
                )
            return jsonify(result)
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/sample")
    def api_enroll_sample():
        if request.mimetype != "multipart/form-data":
            return json_error("multipart/form-data content type required", 415)
        session_id = _bounded_string(
            request.form.get("session_id") or request.form.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        image = request.files.get("image")
        if image is None:
            return json_error("image is required", 400)
        if (image.mimetype or "").lower() not in {"image/jpeg", "image/jpg"}:
            return json_error("image must be a JPEG", 415)
        image_bytes = image.stream.read(MAX_CLIENT_IMAGE_BYTES + 1)
        if len(image_bytes) > MAX_CLIENT_IMAGE_BYTES:
            return json_error("image is too large", 413)
        _jpeg_dimensions(image_bytes)
        try:
            return jsonify(runtime_impl.add_client_enrollment_sample(session_id, image_bytes))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/finish")
    def api_enroll_finish():
        body = json_object()
        session_id = _bounded_string(
            body.get("session_id") or body.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        try:
            result = runtime_impl.finish_client_enrollment(session_id)
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        return jsonify(result), 200 if result.get("ok") else 503

    @app.get("/api/enroll/status")
    def api_enroll_status():
        session_id = _bounded_string(
            request.args.get("session_id") or request.args.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
        try:
            return jsonify(runtime_impl.enrollment_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/cancel")
    def api_enroll_cancel():
        body = json_object()
        session_id = _bounded_string(
            body.get("session_id") or body.get("sessionId"),
            "session_id",
            MAX_IDENTIFIER_CHARS,
            required=True,
        )
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


if __name__ == "__main__":
    raise SystemExit(
        "The unauthenticated standalone Device API is retired. "
        "Start the authenticated service with bass_wireless.py."
    )
