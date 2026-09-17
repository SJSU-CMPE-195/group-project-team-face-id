"""Authentication and device metadata for the wireless API entrypoint."""

from __future__ import annotations

import base64
import hmac
from pathlib import Path

from flask import Flask, jsonify, request

from .config import DeviceConfig
from .local_dashboard import (
    LOCAL_PAIRING_PATH,
    LocalDashboardMiddleware,
    trusted_pairing_request,
)
from .qr_export import render_pairing_qr


def _authorized(pairing_key: str) -> bool:
    scheme, separator, credential = request.headers.get(
        "Authorization", ""
    ).partition(" ")
    if not separator or scheme.lower() != "bearer":
        return False
    try:
        credential_bytes = credential.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(credential_bytes, pairing_key.encode("ascii"))


def secure_wireless_app(
    app: Flask,
    *,
    config: DeviceConfig,
    mode: str,
    port: int,
    dist_root: Path,
) -> Flask:
    def local_pairing_headers(response):
        if request.path != LOCAL_PAIRING_PATH:
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
            return jsonify({"ok": True, "service": "bass-wireless"})
        protected = request.path.startswith("/api/") or request.path == "/ready"
        if protected and not _authorized(config.pairing_key):
            response = jsonify({"ok": False, "error": "unauthorized"})
            response.status_code = 401
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        return None

    @app.get("/api/device-info")
    def device_info():
        return jsonify(
            {
                "device_id": config.device_id,
                "protocol_version": config.version,
                "name": config.name,
                "capabilities": {
                    "client_camera": True,
                    "client_camera_enrollment": True,
                    "client_camera_verification": False,
                    "device_camera": True,
                    "camera_source": "pi_camera" if mode == "pi" else "pc_webcam",
                    "pi_camera": mode == "pi",
                    "simulated_actuators": mode == "pc",
                },
            }
        )

    app.wsgi_app = LocalDashboardMiddleware(
        app.wsgi_app,
        dist_root=dist_root,
        pairing_key=config.pairing_key,
        port=port,
    )

    return app
