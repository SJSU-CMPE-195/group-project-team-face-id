"""Bounded host-browser sessions; the loopback bridge grants no authority."""

from __future__ import annotations

from dataclasses import dataclass
import hmac
import secrets
import threading
import time

from flask import jsonify, request

from pi_device_api import json_object
from .local_dashboard import trusted_dashboard_request
from .security import SecurityError


@dataclass(frozen=True)
class BrowserSession:
    device_token: str
    csrf: str
    expires: float


class LocalSessions:
    def __init__(self, security, port: int, commissioning=None, *, device_id: str):
        self.security = security
        self.port = port
        self.commissioning = commissioning
        self.device_id = device_id
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.RLock()

    def require_trusted(self):
        if not trusted_dashboard_request(request.environ, self.port):
            raise SecurityError("local_only", 403, "Open the dashboard on this host through localhost.")

    def authenticate(self, *, csrf: bool = True):
        self.require_trusted()
        with self._lock:
            cookie = request.cookies.get("bass_session", "")
            session = self._sessions.get(cookie)
            if session is None or session.expires <= time.monotonic():
                self._sessions.pop(cookie, None)
                raise SecurityError("login_required", 401, "Sign in to the host dashboard.")
            if csrf and request.method not in {"GET", "HEAD", "OPTIONS"}:
                supplied = request.headers.get("X-BASS-CSRF", "")
                if not hmac.compare_digest(supplied.encode(), session.csrf.encode()):
                    raise SecurityError("invalid_csrf", 403, "Refresh the dashboard and try again.")
        # Do not hold the session lock while acquiring the shared product
        # barrier: a concurrent login/reset takes these locks in that order.
        return self.security.principal(session.device_token), session

    def register(self, app):
        @app.get("/local/security/status")
        def security_status():
            self.require_trusted()
            state = self.commissioning.status() if self.commissioning else {}
            return jsonify({
                "configured": self.security.configured(),
                "device_id": self.device_id,
                "generation": 0,
                **state,
            })

        @app.post("/local/security/initial-admin")
        def initial_admin():
            self.require_trusted()
            raise SecurityError(
                "phone_claim_required", 410,
                "Scan the activation card on your phone and hold the device button "
                "for three seconds to claim this device.",
            )

        @app.post("/local/security/login")
        def login():
            self.require_trusted()
            body = json_object()
            if ("name" in body) == ("user_id" in body):
                raise SecurityError(
                    "invalid_login_target", 400, "Select one account for sign-in.",
                )
            result = self.security.local_login(
                body.get("name"), body.get("pin"), user_id=body.get("user_id"),
            )
            with self._lock:
                now = time.monotonic()
                expired = [key for key, item in self._sessions.items() if item.expires <= now]
                for key in expired:
                    self._sessions.pop(key, None)
                if len(self._sessions) >= 64:
                    self.security.revoke_local(result["device_token"])
                    raise SecurityError("session_limit", 429, "Too many dashboard sessions; try later.")
                previous = self._sessions.pop(request.cookies.get("bass_session", ""), None)
                if previous:
                    self.security.revoke_local(previous.device_token)
                cookie = secrets.token_hex(32)
                session = BrowserSession(result["device_token"], secrets.token_hex(32), now + 900)
                self._sessions[cookie] = session
            response = jsonify({"user": result["user"], "csrf_token": session.csrf})
            response.set_cookie("bass_session", cookie, max_age=900, httponly=True,
                                samesite="Strict", path="/local")
            return response

        @app.get("/local/security/session")
        def session_info():
            principal, session = self.authenticate()
            return jsonify({"user": user_payload(principal), "csrf_token": session.csrf})

        @app.post("/local/security/logout")
        def logout():
            _, session = self.authenticate()
            with self._lock:
                self._sessions.pop(request.cookies.get("bass_session", ""), None)
                self.security.revoke_local(session.device_token)
            response = jsonify({"ok": True})
            response.delete_cookie("bass_session", path="/local", httponly=True, samesite="Strict")
            return response


def user_payload(principal):
    return {"id": principal.user_id, "name": principal.name,
            "is_admin": principal.is_admin,
            "is_owner": getattr(principal, "is_owner", False)}
