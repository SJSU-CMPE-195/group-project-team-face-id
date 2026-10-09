"""Loopback-only Flask application for the Windows BLE client."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, Response, jsonify, redirect, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

from .ble_http import JsonRequest
from .local_dashboard import trusted_dashboard_request
from .pc_client import OperationGrant, PcClientController, PcClientError


SETUP_COOKIE = "bass_pc_client"
AUTH_COOKIE = "bass_pc_session"
SETUP_SESSION_SECONDS = 60 * 60
AUTH_SESSION_SECONDS = 15 * 60
GRANT_SECONDS = 30
MAX_SESSIONS = 64
MAX_GRANTS = 64
MAX_JSON_REQUEST_BYTES = 64 * 1024
MAX_CARD_REQUEST_BYTES = 1024 * 1024 + 4096
PIN_PATTERN = re.compile(r"^[0-9]{6}$")
ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{0,128}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class _SetupSession:
    csrf: str
    expires: float


@dataclass(frozen=True)
class _AuthSession:
    user: dict[str, object]
    csrf: str
    generation: int
    connection_epoch: int
    expires: float


@dataclass(frozen=True)
class _DeferredGrant:
    session_token: str
    generation: int
    connection_epoch: int
    pin: str
    action: str
    target_user_id: str
    expires: float


class _BrowserSessions:
    def __init__(self, controller: PcClientController) -> None:
        self._controller = controller
        self._lock = threading.RLock()
        self._setup: dict[str, _SetupSession] = {}
        self._auth: dict[str, _AuthSession] = {}
        self._grants: dict[str, _DeferredGrant] = {}
        self._recovery_downloaded: dict[str, int] = {}

    def bootstrap_setup(self) -> tuple[str, _SetupSession]:
        cookie = request.cookies.get(SETUP_COOKIE, "")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            existing = self._setup.get(cookie)
            if existing is not None:
                refreshed = _SetupSession(
                    existing.csrf,
                    now + SETUP_SESSION_SECONDS,
                )
                self._setup[cookie] = refreshed
                return cookie, refreshed
            self._make_room(self._setup)
            token = secrets.token_hex(32)
            session = _SetupSession(
                secrets.token_hex(32),
                now + SETUP_SESSION_SECONDS,
            )
            self._setup[token] = session
            return token, session

    def require_setup(self) -> tuple[str, _SetupSession]:
        token = request.cookies.get(SETUP_COOKIE, "")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            session = self._setup.get(token)
            if session is None:
                raise PcClientError(
                    "client_session_required",
                    401,
                    "Refresh the PC connection page and try again.",
                )
            self._require_csrf(session.csrf)
            return token, session

    def register_auth(
        self,
        user: Mapping[str, object],
        *,
        generation: int,
        connection_epoch: int,
    ) -> tuple[str, _AuthSession]:
        now = time.monotonic()
        previous = request.cookies.get(AUTH_COOKIE, "")
        with self._lock:
            self._prune(now)
            self._auth.pop(previous, None)
            self._drop_grants(previous)
            self._make_room(self._auth)
            token = secrets.token_hex(32)
            session = _AuthSession(
                deepcopy(dict(user)),
                secrets.token_hex(32),
                generation,
                connection_epoch,
                now + AUTH_SESSION_SECONDS,
            )
            self._auth[token] = session
            return token, session

    def require_auth(self, *, csrf: bool) -> tuple[str, _AuthSession]:
        token = request.cookies.get(AUTH_COOKIE, "")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            session = self._auth.get(token)
            if session is None:
                raise PcClientError(
                    "login_required", 401, "Sign in to the paired BASS account."
                )
            identity = self._controller.credential_snapshot()
            if (
                identity is None
                or session.generation != identity["generation"]
                or session.connection_epoch != identity["connection_epoch"]
                or identity["user_id"] != session.user.get("id")
            ):
                self._auth.pop(token, None)
                self._drop_grants(token)
                raise PcClientError(
                    "login_required", 401, "Sign in to the paired BASS account."
                )
            if csrf:
                self._require_csrf(session.csrf)
            return token, session

    def logout(self) -> None:
        token, _session = self.require_auth(csrf=True)
        with self._lock:
            self._auth.pop(token, None)
            self._drop_grants(token)

    def invalidate_auth(self, token: str) -> None:
        with self._lock:
            self._auth.pop(token, None)
            self._drop_grants(token)

    def invalidate_all_auth(self) -> None:
        with self._lock:
            self._auth.clear()
            self._grants.clear()
            self._recovery_downloaded.clear()

    def mark_recovery_downloaded(self, setup_token: str, generation: int) -> None:
        with self._lock:
            if setup_token not in self._setup:
                raise PcClientError(
                    "client_session_required",
                    401,
                    "Refresh the PC connection page and try again.",
                )
            self._recovery_downloaded[setup_token] = generation

    def require_recovery_downloaded(
        self,
        setup_token: str,
        generation: int,
    ) -> None:
        with self._lock:
            if self._recovery_downloaded.get(setup_token) != generation:
                raise PcClientError(
                    "recovery_download_required",
                    409,
                    "Download the recovery card before acknowledging it.",
                )

    def clear_recovery_downloaded(self, _setup_token: str) -> None:
        with self._lock:
            self._recovery_downloaded.clear()

    def create_grant(
        self,
        *,
        session_token: str,
        session: _AuthSession,
        pin: object,
        action: object,
        target_user_id: object,
    ) -> dict[str, object]:
        if not isinstance(pin, str) or PIN_PATTERN.fullmatch(pin) is None:
            raise PcClientError(
                "invalid_pin", 400, "PIN must contain exactly six ASCII digits."
            )
        if not isinstance(action, str) or ACTION_PATTERN.fullmatch(action) is None:
            raise PcClientError("invalid_request", 400, "Operation action is invalid.")
        if (
            not isinstance(target_user_id, str)
            or TARGET_PATTERN.fullmatch(target_user_id) is None
        ):
            raise PcClientError("invalid_request", 400, "Operation target is invalid.")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            if len(self._grants) >= MAX_GRANTS:
                raise PcClientError(
                    "grant_limit_reached",
                    429,
                    "Too many pending approvals. Wait briefly and try again.",
                )
            token = secrets.token_hex(32)
            self._grants[token] = _DeferredGrant(
                session_token,
                session.generation,
                session.connection_epoch,
                pin,
                action,
                target_user_id,
                now + GRANT_SECONDS,
            )
        return {"grant_token": token, "expires_in": GRANT_SECONDS}

    def consume_grant(
        self,
        token: str,
        *,
        session_token: str,
        session: _AuthSession,
        action: str,
        target_user_id: str,
    ) -> OperationGrant:
        now = time.monotonic()
        with self._lock:
            grant = self._grants.pop(token, None)
        if grant is None or grant.expires <= now:
            raise PcClientError(
                "invalid_operation_grant",
                403,
                "Operation approval is invalid or expired.",
            )
        if (
            grant.session_token != session_token
            or grant.generation != session.generation
            or grant.connection_epoch != session.connection_epoch
            or not hmac.compare_digest(grant.action, action)
            or not hmac.compare_digest(grant.target_user_id, target_user_id)
        ):
            raise PcClientError(
                "invalid_operation_grant",
                403,
                "Operation approval does not match this request.",
            )
        return {
            "pin": grant.pin,
            "action": grant.action,
            "target_user_id": grant.target_user_id,
            "expires_at": grant.expires,
        }

    def _prune(self, now: float) -> None:
        self._setup = {
            key: value for key, value in self._setup.items() if value.expires > now
        }
        self._recovery_downloaded = {
            key: value
            for key, value in self._recovery_downloaded.items()
            if key in self._setup
        }
        expired_auth = [
            key for key, value in self._auth.items() if value.expires <= now
        ]
        for key in expired_auth:
            self._auth.pop(key, None)
            self._drop_grants(key)
        self._grants = {
            key: value for key, value in self._grants.items() if value.expires > now
        }

    @staticmethod
    def _make_room(values: dict[str, Any]) -> None:
        if len(values) < MAX_SESSIONS:
            return
        oldest = min(values, key=lambda key: values[key].expires)
        values.pop(oldest, None)

    def _drop_grants(self, session_token: str) -> None:
        self._grants = {
            key: value
            for key, value in self._grants.items()
            if value.session_token != session_token
        }

    @staticmethod
    def _require_csrf(expected: str) -> None:
        supplied = request.headers.get("X-BASS-CSRF", "")
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise PcClientError(
                "invalid_csrf", 403, "Refresh the page and try again."
            )


def create_pc_client_app(
    *,
    port: int,
    dist_root: Path,
    state_path: Path | None = None,
) -> Flask:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    resolved_dist = Path(dist_root).resolve()
    controller = PcClientController(state_path=state_path)
    sessions = _BrowserSessions(controller)
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CARD_REQUEST_BYTES
    app.extensions["bass_pc_client"] = controller

    @app.before_request
    def require_loopback_browser():
        if not trusted_dashboard_request(request.environ, port):
            raise PcClientError(
                "local_only",
                403,
                "Open this page through localhost on the Windows computer.",
            )
        return None

    @app.after_request
    def secure_response(response: Response):
        response.headers.pop("Access-Control-Allow-Origin", None)
        response.headers.pop("Access-Control-Allow-Credentials", None)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.path.startswith("/local/"):
            response.headers["Cache-Control"] = "no-store"
        if request.path == "/sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
        return response

    @app.errorhandler(PcClientError)
    def client_error(error: PcClientError):
        payload: dict[str, object] = {
            "ok": False,
            "code": error.code,
            "error": str(error),
        }
        if error.code == "commission_outcome_unknown":
            payload["retry_available"] = True
        return jsonify(payload), error.status

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(
            {
                "ok": False,
                "code": "request_too_large",
                "error": "Request body is too large.",
            }
        ), 413

    @app.errorhandler(ValueError)
    def invalid_value(error: ValueError):
        return jsonify(
            {"ok": False, "code": "invalid_request", "error": str(error)}
        ), 400

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException):
        if request.path.startswith("/local/"):
            return jsonify(
                {
                    "ok": False,
                    "code": error.name.lower().replace(" ", "_"),
                    "error": error.description,
                }
            ), error.code
        return error

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception):
        app.logger.exception("PC client request failed", exc_info=error)
        return jsonify(
            {
                "ok": False,
                "code": "internal_error",
                "error": "The PC client could not complete this request.",
            }
        ), 500

    @app.get("/local/client/status")
    def client_status():
        token, session = sessions.bootstrap_setup()
        payload = controller.status()
        payload["csrf_token"] = session.csrf
        response = jsonify(payload)
        _set_cookie(response, SETUP_COOKIE, token, SETUP_SESSION_SECONDS)
        return response

    @app.post("/local/client/card")
    def client_card():
        sessions.require_setup()
        body = _json_object(MAX_CARD_REQUEST_BYTES, allowed={"card"})
        if "card" not in body:
            raise PcClientError("invalid_request", 400, "card is required.")
        return jsonify(controller.stage_card(body["card"]))

    @app.post("/local/client/scan")
    def client_scan():
        sessions.require_setup()
        _json_object(MAX_JSON_REQUEST_BYTES, allowed=set())
        return jsonify(controller.scan())

    @app.post("/local/client/connect")
    def client_connect():
        sessions.require_setup()
        body = _json_object(MAX_JSON_REQUEST_BYTES, allowed={"candidate_id"})
        sessions.invalidate_all_auth()
        return jsonify(controller.connect(body.get("candidate_id")))

    @app.post("/local/client/disconnect")
    def client_disconnect():
        sessions.require_setup()
        _json_object(MAX_JSON_REQUEST_BYTES, allowed=set())
        sessions.invalidate_all_auth()
        return jsonify(controller.disconnect())

    @app.post("/local/client/commission")
    def client_commission():
        sessions.require_setup()
        body = _json_object(
            MAX_JSON_REQUEST_BYTES,
            allowed={"pin", "device_name", "name"},
        )
        return jsonify(
            controller.commission(
                pin=body.get("pin"),
                device_name=body.get("device_name"),
                name=body.get("name"),
            )
        )

    @app.post("/local/client/commission/retry")
    def client_commission_retry():
        sessions.require_setup()
        body = _json_object(MAX_JSON_REQUEST_BYTES, allowed={"pin"})
        return jsonify(controller.retry_commission(pin=body.get("pin")))

    @app.post("/local/client/recovery-card")
    def client_recovery_card():
        setup_token, _setup_session = sessions.require_setup()
        body = _json_object(MAX_JSON_REQUEST_BYTES, allowed={"pin"})
        scope = controller.credential_snapshot()
        if scope is None:
            raise PcClientError(
                "pc_commissioning_required",
                409,
                "Pair this Windows profile before exporting a recovery card.",
            )
        status, payload, card = controller.authorize_recovery_export(
            body.get("pin"),
            expected_generation=scope["generation"],
            expected_connection_epoch=scope["connection_epoch"],
            expected_user_id=scope["user_id"],
        )
        if status != 200:
            return jsonify(payload), status
        assert card is not None
        encoded = (json.dumps(card, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
        response = Response(encoded, content_type="application/json")
        response.headers["Content-Disposition"] = (
            'attachment; filename="bass-recovery-card.json"'
        )
        sessions.mark_recovery_downloaded(setup_token, scope["generation"])
        return response

    @app.post("/local/client/recovery-card/acknowledge")
    def client_recovery_acknowledge():
        setup_token, _setup_session = sessions.require_setup()
        _json_object(MAX_JSON_REQUEST_BYTES, allowed=set())
        scope = controller.credential_snapshot()
        if scope is None:
            raise PcClientError(
                "pc_commissioning_required",
                409,
                "Pair this Windows profile before acknowledging a recovery card.",
            )
        sessions.require_recovery_downloaded(setup_token, scope["generation"])
        result = controller.acknowledge_recovery_card(
            expected_generation=scope["generation"],
            expected_user_id=scope["user_id"],
        )
        sessions.clear_recovery_downloaded(setup_token)
        return jsonify(result)

    @app.post("/local/client/forget")
    def client_forget():
        sessions.require_setup()
        body = _json_object(MAX_JSON_REQUEST_BYTES, allowed={"confirm"})
        result = controller.forget(confirm=body.get("confirm"))
        sessions.invalidate_all_auth()
        response = jsonify(result)
        response.delete_cookie(
            AUTH_COOKIE,
            path="/local",
            httponly=True,
            samesite="Strict",
        )
        return response

    @app.get("/local/security/status")
    def security_status():
        return jsonify(controller.security_status())

    @app.post("/local/security/initial-admin")
    def initial_admin():
        sessions.require_setup()
        raise PcClientError(
            "pc_commissioning_required",
            410,
            "Import an activation, invitation, or recovery card on the PC connection page.",
        )

    @app.post("/local/security/login")
    def security_login():
        sessions.require_setup()
        body = _json_object(
            MAX_JSON_REQUEST_BYTES,
            allowed={"name", "user_id", "pin"},
        )
        scope = controller.credential_snapshot()
        if scope is None:
            raise PcClientError(
                "pc_commissioning_required",
                409,
                "Pair this Windows profile before signing in.",
            )
        has_name = "name" in body
        has_user_id = "user_id" in body
        if has_name == has_user_id:
            raise PcClientError(
                "invalid_login_target", 400, "Select one account for sign-in."
            )
        supplied_identity = body["name"] if has_name else body["user_id"]
        expected_identity = scope["user_name"] if has_name else scope["user_id"]
        if supplied_identity != expected_identity:
            raise PcClientError(
                "wrong_account",
                403,
                "This Windows profile is paired to a different account.",
            )
        status, payload = controller.session_login(
            body.get("pin"),
            expected_generation=scope["generation"],
            expected_connection_epoch=scope["connection_epoch"],
            expected_user_id=scope["user_id"],
        )
        if status != 200:
            return jsonify(payload), status
        user = _validated_remote_user(payload, scope)
        with controller.credential_scope(
            generation=scope["generation"],
            connection_epoch=scope["connection_epoch"],
            user_id=scope["user_id"],
        ):
            token, session = sessions.register_auth(
                user,
                generation=scope["generation"],
                connection_epoch=scope["connection_epoch"],
            )
        response = jsonify({"user": user, "csrf_token": session.csrf})
        _set_cookie(response, AUTH_COOKIE, token, AUTH_SESSION_SECONDS)
        return response

    @app.get("/local/security/session")
    def security_session():
        _token, session = sessions.require_auth(csrf=False)
        return jsonify({"user": session.user, "csrf_token": session.csrf})

    @app.post("/local/security/logout")
    def security_logout():
        sessions.logout()
        response = jsonify({"ok": True})
        response.delete_cookie(
            AUTH_COOKIE,
            path="/local",
            httponly=True,
            samesite="Strict",
        )
        return response

    @app.route(
        "/local/wireless/api/<path:api_path>",
        methods=["GET", "POST", "PATCH", "DELETE"],
    )
    def proxy_api(api_path: str):
        csrf_required = request.method not in {"GET", "HEAD", "OPTIONS"}
        session_token, session = sessions.require_auth(csrf=csrf_required)
        if api_path == "operation-grants":
            if request.method != "POST":
                raise PcClientError("method_not_allowed", 405, "Method not allowed.")
            body = _json_object(
                MAX_JSON_REQUEST_BYTES,
                allowed={"pin", "action", "target_user_id"},
            )
            if set(body) != {"pin", "action", "target_user_id"}:
                raise PcClientError(
                    "invalid_request", 400, "PIN, action, and target are required."
                )
            return jsonify(
                sessions.create_grant(
                    session_token=session_token,
                    session=session,
                    pin=body["pin"],
                    action=body["action"],
                    target_user_id=body["target_user_id"],
                )
            )

        remote_path = f"/api/{api_path}"
        route_path = remote_path
        if request.query_string:
            try:
                query = request.query_string.decode("ascii")
            except UnicodeDecodeError as exc:
                raise PcClientError(
                    "invalid_request", 400, "Query string is invalid."
                ) from exc
            remote_path = f"{remote_path}?{query}"
        if not _allowed_api(request.method, route_path):
            if route_path in {"/api/camera/frame", "/api/camera/stream"}:
                raise PcClientError(
                    "unsupported_over_ble",
                    501,
                    "Live camera images are unavailable over the PC BLE connection.",
                )
            raise PcClientError("route_not_found", 404, "Local API route not found.")

        body = None
        if request.method in {"POST", "PATCH"} and request.content_length:
            body = _json_object(MAX_JSON_REQUEST_BYTES)
        action_target = _operation_action_target(
            request.method,
            route_path,
            body,
            str(session.user["id"]),
        )
        grant = None
        supplied_grant = request.headers.get("X-BASS-Operation-Grant", "")
        if supplied_grant:
            if action_target is None:
                action_target = ("", "")
            grant = sessions.consume_grant(
                supplied_grant,
                session_token=session_token,
                session=session,
                action=action_target[0],
                target_user_id=action_target[1],
            )

        if route_path in {
            "/api/lock",
            "/api/ignition/stop",
            "/api/full-reset",
            "/api/scan/start",
        }:
            raise PcClientError(
                "local_controls_required",
                403,
                "Vehicle controls are available only on the Raspberry Pi host dashboard.",
            )

        remote_response = controller.proxy_json(
            JsonRequest(request.method, remote_path, body),
            expected_generation=session.generation,
            expected_connection_epoch=session.connection_epoch,
            expected_user_id=str(session.user["id"]),
            grant=grant,
        )
        payload = remote_response.body
        status = remote_response.status
        if grant is not None and status == 401:
            try:
                error_payload = remote_response.json()
            except Exception:
                error_payload = None
            if isinstance(error_payload, dict) and error_payload.get("code") in {
                "invalid_credentials",
                "invalid_pin",
            }:
                status = 403
        if remote_response.status == 401:
            try:
                error_payload = remote_response.json()
            except Exception:
                error_payload = None
            if isinstance(error_payload, dict) and error_payload.get("code") in {
                "invalid_device_credential",
                "login_required",
            }:
                sessions.invalidate_auth(session_token)
        response = Response(payload, status=status, content_type="application/json")
        return response

    @app.get("/")
    def root_redirect():
        return redirect("/pc-client", code=302)

    @app.get("/<path:asset_path>")
    def dashboard(asset_path: str):
        if asset_path == "api" or asset_path.startswith(("api/", "local/")):
            raise PcClientError("route_not_found", 404, "Local route not found.")
        index_path = resolved_dist / "index.html"
        if not index_path.is_file():
            raise PcClientError(
                "dashboard_unavailable",
                503,
                "Dashboard build unavailable. Build the web application first.",
            )
        requested = asset_path or "index.html"
        try:
            candidate = (resolved_dist / requested).resolve()
            candidate.relative_to(resolved_dist)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PcClientError(
                "asset_not_found", 404, "Dashboard file not found."
            ) from exc
        if not candidate.is_file():
            if Path(requested).suffix:
                raise PcClientError(
                    "asset_not_found", 404, "Dashboard file not found."
                )
            candidate = index_path
        return send_file(candidate, conditional=True)

    return app


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        httponly=True,
        samesite="Strict",
        path="/local",
    )


def _json_object(
    max_bytes: int,
    *,
    allowed: set[str] | None = None,
) -> dict[str, object]:
    if request.content_length is not None and request.content_length > max_bytes:
        raise PcClientError("request_too_large", 413, "Request body is too large.")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise PcClientError("invalid_request", 400, "Request body must be a JSON object.")
    if allowed is not None and not set(body).issubset(allowed):
        raise PcClientError("invalid_request", 400, "Request contains unsupported fields.")
    return body


def _validated_remote_user(
    payload: Mapping[str, object],
    identity: Mapping[str, str],
) -> dict[str, object]:
    user = payload.get("user")
    if (
        not isinstance(user, dict)
        or user.get("id") != identity["user_id"]
        or not isinstance(user.get("name"), str)
        or not user["name"]
        or type(user.get("is_admin")) is not bool
        or type(user.get("is_owner")) is not bool
    ):
        raise PcClientError(
            "device_identity_mismatch",
            409,
            "The Pi returned a different paired account identity.",
        )
    return {
        "id": user["id"],
        "name": user["name"],
        "is_admin": user["is_admin"],
        "is_owner": user["is_owner"],
    }


def _allowed_api(method: str, path: str) -> bool:
    fixed = {
        "GET": {
            "/api/status",
            "/api/device-info",
            "/api/me",
            "/api/camera/status",
            "/api/users",
            "/api/devices",
            "/api/logs",
            "/api/settings",
            "/api/scan/status",
            "/api/enroll/status",
        },
        "POST": {
            "/api/lock",
            "/api/ignition/stop",
            "/api/full-reset",
            "/api/users",
            "/api/pairing-invites",
            "/api/settings",
            "/api/scan/start",
            "/api/scan/cancel",
            "/api/enroll/start",
            "/api/enroll/cancel",
        },
        "PATCH": set(),
        "DELETE": set(),
    }
    if path in fixed.get(method, set()):
        return True
    segments = path.removeprefix("/api/").split("/")
    if any(IDENTIFIER_PATTERN.fullmatch(item) is None for item in segments):
        return False
    if method == "GET":
        return len(segments) == 3 and segments[:2] == ["enroll", "requests"]
    if method == "DELETE":
        return len(segments) == 2 and segments[0] == "users"
    if method == "PATCH":
        return (
            len(segments) == 3
            and segments[0] == "users"
            and segments[2] in {"access", "role"}
        )
    if method == "POST":
        return (
            len(segments) == 3
            and (
                (segments[0] == "users" and segments[2] == "pin")
                or (segments[0] == "devices" and segments[2] == "revoke")
            )
        )
    return False


def _operation_action_target(
    method: str,
    path: str,
    body: Mapping[str, object] | None,
    user_id: str,
) -> tuple[str, str] | None:
    fixed = {
        ("POST", "/api/lock"): ("device.lock", ""),
        ("POST", "/api/ignition/stop"): ("ignition.stop", ""),
        ("POST", "/api/full-reset"): ("device.reset", ""),
        ("POST", "/api/users"): ("user.create", ""),
        ("POST", "/api/settings"): ("settings.update", ""),
    }
    if (method, path) in fixed:
        return fixed[(method, path)]
    if method == "POST" and path == "/api/pairing-invites":
        target = body.get("user_id") if isinstance(body, Mapping) else None
        return ("pairing.invite", target) if isinstance(target, str) else None
    if method == "POST" and path == "/api/scan/start":
        purpose = body.get("purpose", "unlock") if isinstance(body, Mapping) else "unlock"
        if purpose in {"unlock", "ignition"}:
            return f"scan.{purpose}", user_id
        return None
    if method == "POST" and path == "/api/enroll/start":
        target = body.get("user_id") if isinstance(body, Mapping) else None
        return ("enrollment.start", target) if isinstance(target, str) else None
    segments = path.removeprefix("/api/").split("/")
    if len(segments) == 2 and segments[0] == "users" and method == "DELETE":
        return "user.delete", segments[1]
    if len(segments) == 3 and segments[0] == "users":
        action = {
            ("PATCH", "access"): "user.access",
            ("POST", "pin"): "user.pin",
            ("PATCH", "role"): "user.role",
        }.get((method, segments[2]))
        return (action, segments[1]) if action else None
    if (
        len(segments) == 3
        and segments[0] == "devices"
        and segments[2] == "revoke"
        and method == "POST"
    ):
        return "device.revoke", segments[1]
    return None
