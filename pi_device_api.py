"""Canonical Flask Device API for the Raspberry Pi runtime.

The systemd service starts this file. Camera, InsightFace, and ESP32 work is
owned by :class:`car_face_auth.src.pi_runtime.PiRuntime` and happens lazily in
background sessions so this module remains importable off-Pi.
"""

from __future__ import annotations

import atexit
import os
import signal
import time
from pathlib import Path
from typing import Any

from flask import Flask, g, jsonify, request, send_from_directory

from db import init_db
import auth
import db_api
import validation as v
from car_face_auth.src.pi_runtime import PiRuntime, RuntimeRequestError


MAX_CLIENT_IMAGE_BYTES = 8 * 1024 * 1024

# The built frontend, served by this same process so the app and the API share
# an origin.  That is what makes SameSite=Strict session cookies work: a
# cross-site cookie is never sent, so a separately hosted UI could not stay
# signed in.  It also removes CORS from the production path entirely.
DIST_DIR = Path(__file__).resolve().parent / "dist"

# The app pulls its typeface from Google Fonts (see index.html), so those two
# origins are permitted for stylesheets and font files and nothing else.
# connect-src stays 'self': with the Pi serving the app, every API call is
# same-origin, so a script that did get injected has nowhere to send data.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
))

# Liveness/readiness probes carry no data and must stay reachable for systemd
# and install.sh.  Every other route is authenticated, including ones added
# later: the gate is deny-by-default rather than an opt-in decorator.
PUBLIC_PATHS = frozenset({"/health", "/ready"})

def _parse_origins(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip().rstrip("/") for part in raw.split(",") if part.strip())


def create_app(
    db_module: Any = db_api,
    runtime: PiRuntime | None = None,
    api_token: str | None = None,
    idle_seconds: int | None = None,
) -> Flask:
    """Build the API, with a small injection seam for off-Pi route tests.

    ``api_token`` is the legacy shared secret.  It still authenticates as an
    administrator so existing installs keep working through the migration to
    cookie sessions; it is not how browsers are meant to authenticate.
    """

    runtime_impl = runtime or PiRuntime(db_module)
    app = Flask(__name__)

    # Werkzeug rejects an oversized body while parsing, before any view runs.
    # The per-file check inside the upload routes could never do that: Flask had
    # already buffered the whole request, so the 8 MB limit was cosmetic and a
    # multi-gigabyte "image" was a trivial way to exhaust memory on a Pi.
    app.config["MAX_CONTENT_LENGTH"] = MAX_CLIENT_IMAGE_BYTES + (256 * 1024)

    legacy_token = (
        api_token if api_token is not None else os.environ.get("FACEID_API_TOKEN", "")
    ).strip()
    allowed_origins = _parse_origins(os.environ.get("FACEID_ALLOWED_ORIGINS"))

    # Owner of each in-flight enrollment session, so a signed-in driver cannot
    # feed frames into somebody else's enrollment by guessing its id.  Kept
    # here rather than in PiRuntime: it is an authorization concern, not a
    # hardware one.
    enroll_owners: dict[str, str] = {}

    limiter = auth.install(
        app,
        db_module,
        legacy_admin_token=legacy_token,
        allowed_origins=allowed_origins,
        idle_seconds=idle_seconds,
    )

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

    @app.errorhandler(v.ValidationError)
    def handle_validation_error(exc):
        return json_error(str(exc), exc.status_code)

    @app.errorhandler(413)
    def handle_too_large(_exc):
        return json_error("request body is too large", 413)

    @app.errorhandler(429)
    def handle_rate_limited(_exc):
        return json_error("too many requests", 429)

    @app.errorhandler(500)
    def handle_internal_error(exc):
        # A stack trace is an information leak and an HTML page breaks every
        # client that expects JSON.  The detail goes to the journal instead.
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        return json_error("internal server error", 500)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        # Werkzeug HTTP exceptions carry their own status and are already
        # handled above; anything else is a bug and must still answer JSON.
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        app.logger.exception("Unhandled error on %s %s", request.method, request.path)
        return json_error("internal server error", 500)

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
        # Authentication is a cookie now, so a wildcard origin is not merely
        # loose -- it is unusable: browsers refuse to send credentials to
        # Access-Control-Allow-Origin "*".  Credentials are therefore granted
        # only to origins named in FACEID_ALLOWED_ORIGINS.  The supported
        # deployment is same-origin (the Pi serves the app), where none of
        # this applies at all.
        origin = request.headers.get("Origin")
        if allowed_origins:
            if origin and origin.rstrip("/") in allowed_origins:
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Vary"] = "Origin"
        else:
            # No allowlist: readable cross-origin, but never with credentials.
            resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = (
            "Authorization, X-API-Key, Content-Type, Cache-Control, Pragma"
        )
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"

        # Applied to every response, including errors and static assets.
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault(
            "Permissions-Policy",
            # The app uses the camera on its own origin only.
            "camera=(self), microphone=(), geolocation=(), interest-cohort=()",
        )
        resp.headers.setdefault(
            "Content-Security-Policy",
            os.environ.get("FACEID_CSP") or CONTENT_SECURITY_POLICY,
        )
        if app.config.get("FACEID_REQUIRE_HTTPS"):
            # Only meaningful once TLS terminates in front of this service;
            # sending it over plain HTTP would strand clients on a broken URL.
            resp.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
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
        reason = v.require_str(body, "reason", max_len=v.MAX_REASON,
                               required=False, default="manual_ui")
        result = runtime_impl.unlock(reason=reason or "manual_ui")
        return (jsonify(result), 200 if result.get("ok") else 503)

    @app.post("/api/lock")
    def api_lock():
        body = json_object()
        reason = v.require_str(body, "reason", max_len=v.MAX_REASON,
                               required=False, default="manual_ui")
        result = runtime_impl.force_lock(reason=reason or "manual_ui")
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
        name = v.require_str(body, "name", max_len=v.MAX_NAME)
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
        allowed = v.require_bool(body, "allowed")
        body["allowed"] = allowed
        set_user_access = getattr(runtime_impl, "set_user_access", db_module.set_user_access)
        result = set_user_access(user_id, body["allowed"])
        return jsonify(result), 200 if result.get("ok") else 404

    @app.post("/api/verify-log")
    def api_verify_log():
        body = json_object()
        db_module.log_event(
            stage="face_verify",
            result=v.one_of(
                v.require_str(body, "result", max_len=32, required=False, default="unknown"),
                ("ok", "fail", "unknown", "denied", "granted", "error"),
                "result",
            ),
            detail=v.require_str(body, "detail", max_len=v.MAX_DETAIL,
                                 required=False, default=""),
            user_id=v.require_str(body, "user_id", max_len=64,
                                  required=False, default="") or None,
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
        db_module.save_settings_from_ui(v.validate_settings(body))
        return jsonify({"ok": True})

    @app.post("/api/scan/start")
    def api_scan_start():
        body = json_object()
        source = str(body.get("source") or "pi_camera").strip().lower()
        try:
            scan_args = {
                "purpose": v.one_of(
                    v.require_str(body, "purpose", max_len=16, required=False,
                                  default="unlock") or "unlock",
                    ("unlock", "ignition"), "purpose",
                ),
                "expected_user": v.require_str(
                    body, "expected_user", max_len=v.MAX_NAME, required=False,
                    default="", aliases=("expectedUser",),
                ) or None,
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
        session_id = v.session_id(request.form, "session_id", "sessionId")
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
        session_id = v.session_id(request.args, "session_id", "sessionId")
        try:
            return jsonify(runtime_impl.scan_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/scan/cancel")
    def api_scan_cancel():
        body = json_object()
        session_id = v.session_id(body, "session_id", "sessionId")
        try:
            return jsonify(runtime_impl.cancel_scan(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    def enroll_session_denied(session_id: str):
        """Reject work on an enrollment session the caller does not own."""
        if auth.is_admin():
            return None
        owner = enroll_owners.get(session_id)
        if owner is None:
            # Unknown session: let the runtime answer 404 rather than leaking
            # whether the id exists.
            return None
        if owner == (auth.current_user() or {}).get("id"):
            return None
        return json_error("not permitted for this account", 403)

    @app.post("/api/enroll/start")
    def api_enroll_start():
        body = json_object()
        source = str(body.get("source") or "pi_camera").strip().lower()
        user = auth.current_user() or {}
        # A non-admin may only enroll their own face.  The client-supplied name
        # is ignored entirely for them -- otherwise anyone signed in could
        # enroll their face onto another driver's account and unlock the car.
        if auth.is_admin():
            target_name = v.require_str(body, "name", max_len=v.MAX_NAME)
        else:
            target_name = user.get("name") or ""
            if not target_name:
                return json_error("this account cannot enroll a face", 403)
        try:
            if source == "pi_camera":
                result = runtime_impl.start_enrollment(target_name)
            elif source in {"client_camera", "device_camera", "phone_camera"}:
                result = runtime_impl.start_client_enrollment(target_name)
            else:
                raise RuntimeRequestError("source must be pi_camera or client_camera", 400)
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        started = result.get("session_id") if isinstance(result, dict) else None
        if started:
            enroll_owners[started] = user.get("id")
        return jsonify(result)

    @app.post("/api/enroll/sample")
    def api_enroll_sample():
        session_id = v.session_id(request.form, "session_id", "sessionId")
        denied = enroll_session_denied(session_id)
        if denied:
            return denied
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
        session_id = v.session_id(body, "session_id", "sessionId")
        denied = enroll_session_denied(session_id)
        if denied:
            return denied
        try:
            result = runtime_impl.finish_client_enrollment(session_id)
        except RuntimeRequestError as exc:
            return runtime_error(exc)
        return jsonify(result), 200 if result.get("ok") else 503

    @app.get("/api/enroll/status")
    def api_enroll_status():
        session_id = v.session_id(request.args, "session_id", "sessionId")
        denied = enroll_session_denied(session_id)
        if denied:
            return denied
        try:
            return jsonify(runtime_impl.enrollment_status(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    @app.post("/api/enroll/cancel")
    def api_enroll_cancel():
        body = json_object()
        session_id = v.session_id(body, "session_id", "sessionId")
        denied = enroll_session_denied(session_id)
        if denied:
            return denied
        try:
            return jsonify(runtime_impl.cancel_enrollment(session_id))
        except RuntimeRequestError as exc:
            return runtime_error(exc)

    # ── Identity, sessions, pairing ────────────────────────────────────────

    @app.get("/api/me")
    def api_me():
        """The caller's own profile, derived from the session -- never from input."""
        user = auth.current_user()
        return jsonify({
            "ok": True,
            "id": user.get("id"),
            "name": user.get("name"),
            "role": user.get("role"),
            "faceEnrolled": bool(user.get("face_enrolled")),
            "faceAccess": bool(user.get("face_access", True)),
        })

    @app.post("/api/auth/logout")
    def api_logout():
        user = auth.current_user()
        session_id = user.get("session_id")
        if session_id:
            db_module.revoke_session(session_id, user_id=user.get("id"))
            db_module.log_event("logout", "ok", detail="signed out", user_id=user.get("id"))
        resp = jsonify({"ok": True})
        return auth.clear_session_cookie(resp)

    @app.get("/api/sessions")
    def api_my_sessions():
        user = auth.current_user()
        if not user.get("id"):
            return jsonify([])
        return jsonify(db_module.list_sessions_for_user(user["id"]))

    @app.delete("/api/sessions/<session_id>")
    def api_revoke_my_session(session_id):
        # Scoped to the caller's own id, so one driver cannot sign another out.
        user = auth.current_user()
        result = db_module.revoke_session(session_id, user_id=user.get("id"))
        return jsonify(result), 200 if result.get("ok") else 404

    @app.post("/api/pair/create")
    def api_pair_create():
        """Admin mints a one-time code for a user.  Returned in plaintext once."""
        body = json_object()
        admin = auth.current_user()
        user_id = v.require_str(body, "user_id", max_len=64, aliases=("userId",))
        if not db_module.get_user_by_id(user_id):
            return json_error("user not found", 404)
        result = db_module.create_pairing_code(user_id, created_by=admin.get("id"))
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result), 201

    @app.post("/api/pair/redeem")
    def api_pair_redeem():
        """Exchange a one-time code for a session.  The only public write."""
        # Rate limited per source address: the code is the sole proof here, so
        # guessing must be expensive even though it is 192 bits of entropy.
        if not limiter.check(f"pair:{request.remote_addr}", limit=5, window_seconds=900):
            db_module.log_event("pair_failed", "fail", detail="rate limited")
            return json_error("too many pairing attempts; try again later", 429)
        body = json_object()
        code = v.require_str(body, "code", max_len=v.MAX_CODE)
        claimed = db_module.redeem_pairing_code(code)
        if not claimed.get("ok"):
            return json_error(claimed.get("error", "invalid pairing code"), 401)
        issued = db_module.create_session(
            claimed["user_id"], user_agent=request.headers.get("User-Agent", "")
        )
        if not issued.get("ok"):
            return json_error(issued.get("error", "could not create session"), 400)
        profile = db_module.get_user_by_id(claimed["user_id"]) or {}
        resp = jsonify({
            "ok": True,
            "id": profile.get("id"),
            "name": profile.get("name"),
            "role": profile.get("role", "USER"),
            "faceEnrolled": bool(profile.get("face_enrolled")),
        })
        max_age = max(0, issued["expires_at"] - int(time.time()))
        return auth.set_session_cookie(resp, issued["token"], app, max_age), 201

    @app.get("/api/users/<user_id>")
    def api_get_user(user_id):
        denied = auth.require_self_or_admin(user_id)
        if denied:
            return denied
        profile = db_module.get_user_by_id(user_id)
        if not profile:
            return json_error("user not found", 404)
        return jsonify({
            "id": profile["id"],
            "name": profile["name"],
            "role": profile.get("role", "USER"),
            "faceEnrolled": bool(profile.get("face_enrolled")),
            "faceAccess": bool(profile.get("face_access", 1)),
            "createdAt": int(profile["created_at"]) * 1000,
        })

    @app.delete("/api/users/<user_id>/face")
    def api_delete_face(user_id):
        """Remove a face template.  Admins may do it for anyone; a driver only
        for themselves.  This replaces the frontend's direct call to the
        unauthenticated Face API, which accepted a bare name from anybody."""
        denied = auth.require_self_or_admin(user_id)
        if denied:
            return denied
        result = db_module.clear_user_embedding(user_id)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)

    @app.patch("/api/users/<user_id>/role")
    def api_set_role(user_id):
        body = json_object()
        role = (body.get("role") or "").strip().upper()
        if role not in ("ADMIN", "USER"):
            return json_error("role must be ADMIN or USER", 400)
        result = db_module.set_user_role(user_id, role)
        if not result.get("ok"):
            status = 404 if "not found" in result.get("error", "") else 409
            return jsonify(result), status
        # A demoted administrator must not keep admin-level sessions alive.
        if role == "USER":
            db_module.revoke_all_sessions_for_user(user_id)
        return jsonify(result)

    # ── Frontend ───────────────────────────────────────────────────────────

    @app.get("/")
    def serve_index():
        if not DIST_DIR.is_dir():
            return json_error(
                "frontend build not found; run 'npm run build' to create dist/", 404
            )
        return send_from_directory(DIST_DIR, "index.html")

    # Explicit prefixes rather than a catch-all.  A catch-all would also match
    # unknown /api/ paths and, being a GET rule, would turn a wrong-method
    # request on a real endpoint into 404 HTML instead of 405 JSON.  The UI has
    # no client-side routing, so nothing needs the fallback.

    @app.get("/assets/<path:asset>")
    def serve_build_asset(asset):
        # send_from_directory refuses to escape the directory it is given.
        return send_from_directory(DIST_DIR / "assets", asset)

    @app.get("/icons/<path:asset>")
    def serve_icon(asset):
        return send_from_directory(DIST_DIR / "icons", asset)

    @app.get("/sw.js")
    def serve_service_worker():
        # Served from the root so its scope covers the whole origin.
        return send_from_directory(DIST_DIR, "sw.js")

    @app.get("/manifest.webmanifest")
    def serve_manifest():
        return send_from_directory(DIST_DIR, "manifest.webmanifest")

    @app.get("/vite.svg")
    def serve_favicon():
        return send_from_directory(DIST_DIR, "vite.svg")

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
    # Clear out expired sessions, spent pairing codes, and old audit rows.
    try:
        db_api.purge_expired()
    except Exception as exc:  # maintenance must never block startup
        print(f"WARNING: could not purge expired records: {exc}", flush=True)
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)
    startup = _runtime.force_lock(reason="startup")
    if not startup.get("ok"):
        print(f"WARNING: startup fail-safe did not reach the ESP32: {startup.get('error')}", flush=True)
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
