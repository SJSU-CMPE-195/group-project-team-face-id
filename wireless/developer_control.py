"""Loopback-only developer control routes for the hardware simulator."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hmac
from io import BytesIO
import json
import secrets
import threading
import time
from typing import Any, Callable

from flask import jsonify, request

from pi_device_api import json_object
from .local_dashboard import trusted_dashboard_request
from .security import SecurityError


DEV_COOKIE = "bass_dev_session"
SESSION_SECONDS = 30 * 60


@dataclass(frozen=True)
class _DeveloperSession:
    csrf: str
    expires: float

class DeveloperControl:
    """Expose developer controls to the explicitly enabled local dashboard."""

    def __init__(
        self,
        *,
        events,
        reset,
        card_provider: Callable[[], dict[str, Any]],
        port: int,
        clock: Callable[[], float] = time.monotonic,
        session_seconds: float = SESSION_SECONDS,
    ) -> None:
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("dashboard port must be between 1 and 65535")
        if session_seconds <= 0:
            raise ValueError("developer session lifetime must be positive")
        self._events = events
        self._reset = reset
        self._card_provider = card_provider
        self._port = port
        self._clock = clock
        self._session_seconds = float(session_seconds)
        self._sessions: dict[str, _DeveloperSession] = {}
        self._lock = threading.RLock()

    def register(self, app) -> None:
        app.add_url_rule(
            "/local/hardware/status",
            endpoint="hardware_status",
            view_func=self._status,
            methods=["GET"],
        )
        app.add_url_rule(
            "/local/hardware/card",
            endpoint="hardware_card",
            view_func=self._card,
            methods=["GET"],
        )
        for action in ("down", "keepalive", "up", "cancel"):
            app.add_url_rule(
                f"/local/hardware/button/{action}",
                endpoint=f"hardware_button_{action}",
                view_func=self._button_handler(action),
                methods=["POST"],
            )
        app.add_url_rule(
            "/local/hardware/power",
            endpoint="hardware_power",
            view_func=self._power,
            methods=["POST"],
        )
        app.add_url_rule(
            "/local/hardware/reset",
            endpoint="hardware_reset",
            view_func=self._developer_reset,
            methods=["POST"],
        )

    def _status(self):
        self._require_trusted()
        session, cookie = self._ensure_session()
        response = jsonify(self._authenticated_status(session))
        if cookie is not None:
            response.set_cookie(
                DEV_COOKIE,
                cookie,
                max_age=int(self._session_seconds),
                httponly=True,
                samesite="Strict",
                path="/local/hardware",
            )
        return response

    def _card(self):
        self._authenticate(csrf=False)
        material = self._card_provider()
        if not isinstance(material, dict) or not isinstance(
            material.get("public_payload"), dict
        ):
            raise RuntimeError(
                "developer card provider returned invalid material"
            )
        public_payload = material["public_payload"]
        activation_payload = material.get("activation_payload")
        if activation_payload is not None and not isinstance(
            activation_payload, dict
        ):
            raise RuntimeError("activation payload must be an object or null")
        _validate_card_payloads(public_payload, activation_payload)
        return jsonify(
            {
                "public_payload": public_payload,
                "public_qr_image": _qr_data_url(public_payload),
                "activation_payload": activation_payload,
                "activation_qr_image": (
                    _qr_data_url(activation_payload)
                    if activation_payload is not None
                    else None
                ),
            }
        )

    def _button_handler(self, action: str):
        method = getattr(self._events, f"press_{action}")

        def handle_button():
            self._authenticate()
            body = json_object()
            result = method(body.get("press_id"))
            return jsonify(
                {"result": result, "status": self._events.snapshot()}
            )

        return handle_button

    def _power(self):
        self._authenticate()
        body = json_object()
        return jsonify(self._events.power(body.get("powered")))

    def _developer_reset(self):
        self._authenticate()
        body = json_object()
        receipt = self._reset.execute(
            body.get("request_id"), body.get("confirmation")
        )
        return jsonify({"receipt": receipt, "status": self._events.snapshot()})

    def _authenticate(self, *, csrf: bool = True) -> _DeveloperSession:
        self._require_trusted()
        session = self._current_session()
        if session is None:
            raise SecurityError(
                "developer_session_required",
                401,
                "refresh the local hardware simulator page",
            )
        if csrf:
            self._require_exact_origin()
            supplied = request.headers.get("X-BASS-Dev-CSRF", "")
            if not hmac.compare_digest(
                supplied.encode("utf-8"), session.csrf.encode("utf-8")
            ):
                raise SecurityError(
                    "invalid_developer_csrf",
                    403,
                    "refresh the hardware simulator and try again",
                )
        return session

    def _current_session(self) -> _DeveloperSession | None:
        cookie = request.cookies.get(DEV_COOKIE, "")
        with self._lock:
            self._prune_sessions_locked()
            return self._sessions.get(cookie)

    def _ensure_session(self) -> tuple[_DeveloperSession, str | None]:
        cookie = request.cookies.get(DEV_COOKIE, "")
        with self._lock:
            self._prune_sessions_locked()
            session = self._sessions.get(cookie)
            if session is not None:
                return session, None
            if len(self._sessions) >= 64:
                oldest = min(
                    self._sessions,
                    key=lambda key: self._sessions[key].expires,
                )
                self._sessions.pop(oldest, None)
            cookie = secrets.token_hex(32)
            session = _DeveloperSession(
                csrf=secrets.token_hex(32),
                expires=self._clock() + self._session_seconds,
            )
            self._sessions[cookie] = session
            return session, cookie

    def _prune_sessions_locked(self) -> None:
        now = self._clock()
        expired = [
            cookie
            for cookie, session in self._sessions.items()
            if session.expires <= now
        ]
        for cookie in expired:
            self._sessions.pop(cookie, None)

    def _authenticated_status(
        self, session: _DeveloperSession
    ) -> dict[str, Any]:
        pending_request = getattr(self._reset, "pending_request", None)
        pending_reset = pending_request() if callable(pending_request) else None
        return {
            "enabled": True,
            "authenticated": True,
            "csrf_token": session.csrf,
            "pending_reset": pending_reset,
            **self._events.snapshot(),
        }

    def _require_trusted(self) -> None:
        if not trusted_dashboard_request(request.environ, self._port):
            raise SecurityError(
                "local_only",
                403,
                "open the hardware simulator through localhost on this "
                "computer",
            )

    @staticmethod
    def _require_exact_origin() -> None:
        expected_origin = request.host_url.rstrip("/")
        if request.headers.get("Origin") != expected_origin:
            raise SecurityError(
                "invalid_developer_origin",
                403,
                "the hardware simulator request origin is invalid",
            )

def _qr_data_url(payload: dict[str, Any]) -> str:
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_Q
    except ImportError as exc:
        raise RuntimeError(
            "hardware simulator QR rendering requires qrcode and Pillow"
        ) from exc
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_Q,
        box_size=8,
        border=4,
    )
    qr.add_data(encoded)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#140d2b", back_color="white")
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    rendered = base64.b64encode(image_bytes.getvalue()).decode("ascii")
    return f"data:image/png;base64,{rendered}"


def _validate_card_payloads(
    public_payload: dict[str, Any],
    activation_payload: dict[str, Any] | None,
) -> None:
    if (
        public_payload.get("version") != 3
        or public_payload.get("purpose") != "device"
        or "activation_secret" in public_payload
        or "pairing_key" in public_payload
    ):
        raise RuntimeError(
            "public device payload contains invalid or private fields"
        )
    if activation_payload is None:
        return
    secret = activation_payload.get("activation_secret")
    if (
        activation_payload.get("version") != 3
        or activation_payload.get("purpose") != "activation"
        or not isinstance(secret, str)
        or len(secret) != 64
        or any(character not in "0123456789abcdef" for character in secret)
        or activation_payload.get("device_id")
        != public_payload.get("device_id")
        or activation_payload.get("tls_certificate_sha256")
        != public_payload.get("tls_certificate_sha256")
    ):
        raise RuntimeError(
            "activation payload is invalid or does not match the device"
        )
