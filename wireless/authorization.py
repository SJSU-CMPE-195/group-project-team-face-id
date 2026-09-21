"""Canonical operation and session policy for authenticated Device API calls."""

import base64

from flask import g, request

from pi_device_api import json_object
from .qr_export import render_qr_payload
from .security import SecurityError


_ADMIN_ENDPOINTS = {"api_logs", "api_add_user", "api_delete_user", "api_set_access",
                    "api_settings", "api_enroll_start", "pairing_invite", "device_list",
                    "device_revoke", "user_pin"}
_ACTIONS = {
    "api_add_user": "user.create", "api_delete_user": "user.delete",
    "api_set_access": "user.access", "api_settings": "settings.update",
    "api_lock": "device.lock", "api_ignition_stop": "ignition.stop",
    "api_full_reset": "device.reset", "pairing_invite": "pairing.invite",
    "device_revoke": "device.revoke", "user_pin": "user.pin",
}
_SESSION_ENDPOINTS = {
    "api_scan_status", "api_scan_cancel", "api_enroll_status", "api_enroll_cancel",
    "api_enroll_sample", "api_enroll_finish", "api_camera_frame", "api_camera_stream",
}


def authorize_request(security, runtime, db_module):
    actor = g.principal
    endpoint = request.endpoint
    if endpoint in _ADMIN_ENDPOINTS and not actor.is_admin:
        raise SecurityError("admin_required", 403, "Administrator permission is required.")
    if endpoint == "api_unlock":
        raise SecurityError("route_retired", 410, "Use a PIN-authorized host camera scan.")
    if endpoint in _SESSION_ENDPOINTS:
        if request.method == "GET":
            body = request.args
        elif endpoint == "api_enroll_sample":
            body = request.form
        else:
            body = json_object()
        session_id = body.get("session_id") or body.get("sessionId")
        if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
            raise SecurityError("invalid_session", 400, "A valid session ID is required.")
        runtime.require_session_owner(session_id, actor)
        return

    action = _ACTIONS.get(endpoint)
    target = (request.view_args or {}).get("user_id")
    if action in {"device.lock", "ignition.stop", "device.reset"}:
        target = actor.user_id
    if endpoint == "device_revoke":
        target = request.view_args["device_id"]
    elif endpoint == "pairing_invite":
        target = json_object().get("user_id")
    elif endpoint == "api_scan_start":
        body = json_object()
        purpose = body.get("purpose", "unlock")
        if not isinstance(purpose, str) or purpose not in {"unlock", "ignition"}:
            raise SecurityError("invalid_purpose", 400, "Purpose must be unlock or ignition.")
        if body.get("expected_user_id", actor.user_id) != actor.user_id:
            raise SecurityError("wrong_user", 403, "You can only verify your own face.")
        action, target = f"scan.{purpose}", actor.user_id
    elif endpoint == "api_enroll_start":
        body = json_object()
        user_id, name = body.get("user_id"), body.get("name")
        users = db_module.list_users_for_ui()
        if user_id is not None:
            matches = [user for user in users if user["id"] == user_id]
        else:
            matches = [user for user in users if isinstance(name, str)
                       and user["name"].strip().casefold() == name.strip().casefold()]
        if len(matches) != 1:
            raise SecurityError("invalid_user", 400, "Select one existing user for enrollment.")
        g.enrollment_user = matches[0]
        action, target = "enrollment.start", matches[0]["id"]
    if action:
        security.consume_grant(actor, request.headers.get("X-BASS-Operation-Grant", ""),
                               action, target)


def register_security_routes(app, security, config):
    from flask import jsonify
    from .local_security import user_payload

    @app.get("/api/me")
    def current_user():
        return jsonify({"user": user_payload(g.principal), "device_id": g.principal.device_id})

    @app.post("/api/session-login")
    def session_login():
        body = json_object()
        if set(body) != {"pin"}:
            raise SecurityError("invalid_request", 400, "Only the account PIN is required.")
        current = security.confirm_session(g.principal, body.get("pin"))
        return jsonify({"user": user_payload(current), "device_id": current.device_id})

    @app.post("/api/operation-grants")
    def operation_grant():
        body = json_object()
        return jsonify(security.authorize(g.principal, body.get("pin"), body.get("action"),
                                          body.get("target_user_id")))

    @app.post("/api/pairing-invites")
    def pairing_invite():
        invitation = security.issue_invite(
            g.principal,
            json_object().get("user_id"),
        )
        qr_payload = {
            **config.pairing_payload,
            "purpose": "pairing_invite",
            "invite_token": invitation["invite_token"],
        }
        encoded_image = base64.b64encode(render_qr_payload(qr_payload)).decode("ascii")
        return jsonify({
            **invitation,
            "qr_payload": qr_payload,
            "qr_image": f"data:image/png;base64,{encoded_image}",
        }), 201

    @app.get("/api/devices")
    def device_list():
        return jsonify(security.list_devices(g.principal))

    @app.post("/api/devices/<device_id>/revoke")
    def device_revoke(device_id):
        security.revoke_device(g.principal, device_id)
        app.extensions["bass_runtime"].cancel_device_sessions(device_id)
        return jsonify({"ok": True})

    @app.post("/api/users/<user_id>/pin")
    def user_pin(user_id):
        security.set_pin(g.principal, user_id, json_object().get("pin"))
        return jsonify({"ok": True})
