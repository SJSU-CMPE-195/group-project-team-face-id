"""Authentication and device metadata for the wireless API entrypoint."""

from __future__ import annotations

import base64
from pathlib import Path

from flask import Flask, g, jsonify, request

from pi_device_api import json_object

from .config import API_PROTOCOL_VERSION, DeviceConfig
from .local_dashboard import (
    LOCAL_PAIRING_PATH,
    LocalDashboardMiddleware,
    trusted_pairing_request,
)
from .qr_export import render_pairing_qr
from .request_limits import RequestLimits
from .authorization import authorize_request, register_security_routes
from .local_security import LocalSessions
from .security import SecurityError
from .commissioning_routes import (
    PUBLIC_COMMISSIONING_PATHS,
    register_commissioning_routes,
    require_https_commissioning,
)


def _credential(expected_scheme: str) -> str:
    scheme, separator, credential = request.headers.get(
        "Authorization", ""
    ).partition(" ")
    if not separator or scheme.lower() != expected_scheme:
        return ""
    try:
        credential.encode("ascii")
    except UnicodeEncodeError:
        return ""
    return credential if len(credential) <= 128 else ""


def secure_wireless_app(
    app: Flask,
    *,
    config: DeviceConfig,
    mode: str,
    port: int,
    dist_root: Path,
    security,
    commissioning=None,
    events=None,
    developer_control=None,
    transfer_handler=None,
    recovery_handler=None,
) -> Flask:
    if developer_control is not None and mode != "pc":
        raise ValueError("Hardware simulation is available only in PC development mode.")
    limits = RequestLimits()
    local_sessions = LocalSessions(
        security, port, commissioning, device_id=config.device_id,
    )
    app.extensions["bass_security"] = security
    app.extensions["bass_commissioning"] = commissioning
    app.extensions["bass_hardware_events"] = events
    runtime = app.extensions["bass_runtime"]
    db_module = app.extensions["bass_db"]
    runtime.configure_authorization(security)
    local_sessions.register(app)
    register_security_routes(app, security, config)
    if commissioning is not None:
        if transfer_handler is None or recovery_handler is None:
            raise ValueError("Commissioning requires runtime-draining ownership handlers.")
        register_commissioning_routes(
            app, commissioning, events, transfer_handler, recovery_handler,
        )
    if developer_control is not None:
        developer_control.register(app)

    @app.post("/api/pairings")
    def pair_device():
        body = json_object()
        return jsonify(security.pair(
            body.get("invite_token"), body.get("pin"), body.get("device_name"),
            request_id=body.get("request_id"), device_token=body.get("device_token"),
        )), 201

    @app.errorhandler(SecurityError)
    def security_error(exc):
        response = jsonify({"ok": False, "code": exc.code, "error": str(exc)})
        response.status_code = exc.status
        if exc.status == 401:
            response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.teardown_request
    def release_authorization_barrier(_error):
        barrier = g.pop("authorization_barrier", None)
        if barrier:
            barrier.__exit__(None, None, None)

    def local_pairing_headers(response):
        if not request.path.startswith("/local/") and not request.environ.get("bass.local_dashboard"):
            return response
        for header in list(response.headers.keys()):
            if header.lower().startswith("access-control-"):
                response.headers.remove(header)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    # Flask executes these in reverse order. Run last to remove the Device
    # API's wildcard CORS header from this local secret-bearing response.
    app.after_request_funcs.setdefault(None, []).insert(0, local_pairing_headers)

    @app.route(LOCAL_PAIRING_PATH, methods=["GET", "OPTIONS"])
    def local_pairing_qr():
        if not trusted_pairing_request(request.environ, port):
            return jsonify({"error": "Open Settings on this device through localhost."}), 403
        actor, _ = local_sessions.authenticate()
        if not actor.is_admin:
            raise SecurityError("admin_required", 403, "Administrator permission is required.")
        if request.method == "OPTIONS":
            return "", 204
        encoded_image = base64.b64encode(render_pairing_qr(config)).decode("ascii")
        return jsonify(
            {
                "device_id": config.device_id,
                "name": config.name,
                "qr_image": f"data:image/png;base64,{encoded_image}",
            }
        )

    @app.before_request
    def authenticate_wireless_requests():
        if request.path == "/health" and request.method == "GET":
            return jsonify({"ok": True, "service": "bass-wireless",
                            "device_id": config.device_id, "protocol_version": API_PROTOCOL_VERSION})
        credential_path = request.path in {
            "/api/pairings", "/local/security/login", "/local/security/initial-admin"
        } or request.path in PUBLIC_COMMISSIONING_PATHS
        if credential_path:
            if request.path.startswith("/local/"):
                local_sessions.require_trusted()
            retry_after = limits.check(request.remote_addr or "", request.path, request.method)
            if retry_after:
                raise SecurityError("rate_limited", 429, "Too many attempts; try again later.")
        if request.path in PUBLIC_COMMISSIONING_PATHS:
            require_https_commissioning()
            if commissioning is None:
                raise SecurityError("not_found", 404, "Commissioning is unavailable.")
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                json_object()
            if request.path in {
                "/api/commissioning/transfer/accept", "/api/commissioning/recover",
            }:
                # This handler must release the barrier while draining workers;
                # it validates the proof and gates the product before doing so.
                return None
            _hold_product_barrier(
                allow_unavailable=request.path == "/api/commissioning/status",
            )
            return None
        if request.path == "/api/pairings":
            if commissioning is not None:
                require_https_commissioning()
            # The invitation and personal PIN authorize pairing. Public identity
            # QR material is not an authorization credential.
            json_object()
            _hold_product_barrier()
            return None
        if request.path.startswith("/local/security/"):
            local_sessions.require_trusted()
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                json_object()
            _hold_product_barrier(
                allow_unavailable=request.path == "/local/security/status",
            )
            return None
        protected = request.path.startswith("/api/") or request.path == "/ready"
        if protected:
            if request.environ.get("bass.local_dashboard"):
                actor, _ = local_sessions.authenticate()
            else:
                actor = security.principal(_credential("bearer"))
            g.principal = actor
            retry_after = limits.check(request.remote_addr or "", request.path, request.method)
            if retry_after:
                response = jsonify({"ok": False, "error": "too many requests; retry later"})
                response.status_code = 429
                response.headers["Retry-After"] = str(retry_after)
                return response
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                # Read bounded input outside the shared authorization barrier:
                # a slow upload must not hold every user's permission checks.
                if request.endpoint == "api_enroll_sample":
                    request.form
                else:
                    json_object()
            _hold_product_barrier()
            if not security.still_authorized(actor):
                raise SecurityError("unauthorized", 401, "Authorization has changed; sign in again.")
            authorize_request(security, runtime, db_module)
        return None

    def _hold_product_barrier(*, allow_unavailable=False):
        barrier = security.synchronized()
        barrier.__enter__()
        g.authorization_barrier = barrier
        if events is not None and not allow_unavailable:
            events.require_available()

    # Authentication precedes body parsing and other application request hooks.
    hooks = app.before_request_funcs[None]
    hooks.remove(authenticate_wireless_requests)
    hooks.insert(0, authenticate_wireless_requests)

    @app.get("/api/device-info")
    def device_info():
        return jsonify(
            {
                "device_id": config.device_id,
                "protocol_version": API_PROTOCOL_VERSION,
                "transport": "https",
                "name": config.name,
                "capabilities": {
                    "client_camera": True,
                    "client_camera_enrollment": True,
                    "client_camera_verification": False,
                    "device_camera": True,
                    "camera_source": "pi_camera" if mode == "pi" else "pc_webcam",
                    "pi_camera": mode == "pi",
                    "simulated_actuators": mode == "pc",
                    "liveness_available": False,
                    "actuator_feedback": "simulated" if mode == "pc" else "unavailable",
                    "actuator_control_available": mode == "pc",
                    "physical_state_confirmed": False,
                    "phone_ownership": commissioning is not None,
                },
            }
        )

    app.wsgi_app = LocalDashboardMiddleware(
        app.wsgi_app,
        dist_root=dist_root,
        port=port,
    )

    return app
