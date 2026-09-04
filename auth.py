"""Centralized authentication and authorization for the Pi Device API.

Everything security-relevant lives here rather than being sprinkled through
routes, so there is one place to read and one place to get wrong.

The model:

* A browser proves itself once by redeeming a one-time pairing code.
* It receives an HttpOnly cookie holding a high-entropy session token.
* Every later request is identified from that cookie alone.  A user id or role
  supplied in a request body is never trusted -- the identity is looked up
  server-side from the session on each call.
* Access is decided by ENDPOINT_POLICY, keyed by Flask view function name.
  Anything not named there is admin-only, so a route added later starts
  restricted rather than exposed.
"""

from __future__ import annotations

import hmac
import ipaddress
import os
import threading
import time
from collections import deque
from typing import Any

from flask import g, jsonify, request

# ── Policies ───────────────────────────────────────────────────────────────────

POLICY_PUBLIC = "public"                # no session needed
POLICY_USER = "user"                    # any authenticated, active account
POLICY_ADMIN = "admin"                  # role == ADMIN
POLICY_SELF_OR_ADMIN = "self_or_admin"  # gate here, ownership checked in the view

ROLE_ADMIN = "ADMIN"
ROLE_USER = "USER"

# Deny by default: an endpoint absent from this map is treated as ADMIN.  A test
# asserts every registered route appears here, so adding a route forces an
# explicit decision rather than silently inheriting access.
ENDPOINT_POLICY: dict[str, str] = {
    # Unauthenticated surface, deliberately tiny.
    "health": POLICY_PUBLIC,
    "ready": POLICY_PUBLIC,
    "preflight": POLICY_PUBLIC,
    # The built frontend itself. It contains no secrets -- authentication
    # happens after it loads, against the API.
    "serve_index": POLICY_PUBLIC,
    "serve_build_asset": POLICY_PUBLIC,
    "serve_icon": POLICY_PUBLIC,
    "serve_service_worker": POLICY_PUBLIC,
    "serve_manifest": POLICY_PUBLIC,
    "serve_favicon": POLICY_PUBLIC,
    "api_pair_redeem": POLICY_PUBLIC,        # rate limited; the code is the proof

    # Any signed-in driver.
    "api_me": POLICY_USER,
    "api_logout": POLICY_USER,
    "api_my_sessions": POLICY_USER,
    "api_revoke_my_session": POLICY_USER,
    "api_status": POLICY_USER,
    "api_unlock": POLICY_USER,
    "api_lock": POLICY_USER,
    "api_ignition_stop": POLICY_USER,
    "api_full_reset": POLICY_USER,
    "api_scan_start": POLICY_USER,
    "api_scan_sample": POLICY_USER,
    "api_scan_status": POLICY_USER,
    "api_scan_cancel": POLICY_USER,

    # Ownership decided inside the view via require_self_or_admin().
    "api_get_user": POLICY_SELF_OR_ADMIN,
    "api_enroll_start": POLICY_SELF_OR_ADMIN,
    "api_enroll_sample": POLICY_SELF_OR_ADMIN,
    "api_enroll_finish": POLICY_SELF_OR_ADMIN,
    "api_enroll_status": POLICY_SELF_OR_ADMIN,
    "api_enroll_cancel": POLICY_SELF_OR_ADMIN,
    "api_delete_face": POLICY_SELF_OR_ADMIN,

    # Administration.  Enrollment of *other* people, user management, the audit
    # trail, and settings (which can switch off security controls) all sit here.
    "api_users": POLICY_ADMIN,
    "api_add_user": POLICY_ADMIN,
    "api_delete_user": POLICY_ADMIN,
    "api_set_access": POLICY_ADMIN,
    "api_set_role": POLICY_ADMIN,
    "api_face_status": POLICY_ADMIN,
    "api_logs": POLICY_ADMIN,
    "api_get_settings": POLICY_ADMIN,
    "api_settings": POLICY_ADMIN,
    "api_verify_log": POLICY_ADMIN,
    "api_pair_create": POLICY_ADMIN,
    "api_admin_sessions": POLICY_ADMIN,
}

SESSION_COOKIE = "faceid_session"
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ── Rate limiting ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Fixed-memory sliding window, keyed by caller.

    In-process only: it resets on restart and is not shared across workers.
    Adequate for one Pi serving a handful of phones, and it avoids adding a
    Redis-backed dependency to a device that has neither.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """True when the call is allowed; False when the caller is over budget."""
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            # Opportunistic cleanup so an attacker cycling keys cannot grow this
            # dict without bound.
            if len(self._hits) > 2048:
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_loopback(addr: str | None) -> bool:
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def _legacy_token_presented() -> str:
    """The pre-session shared token, read from the same headers as before."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return (request.headers.get("X-API-Key") or "").strip()


def current_user() -> dict[str, Any] | None:
    """The authenticated account for this request, or None."""
    return getattr(g, "user", None)


def is_admin() -> bool:
    user = current_user()
    return bool(user and user.get("role") == ROLE_ADMIN)


def require_self_or_admin(resource_user_id: str):
    """Ownership check for routes addressed by a user id in the URL.

    Compares against the identity derived from the session cookie, never
    against anything in the request body or path -- that is what stops one
    signed-in driver from reading or editing another's record by editing the
    URL.  Returns an error response to hand back, or None when allowed.
    """
    user = current_user()
    if not user:
        return jsonify({"ok": False, "error": "authentication required"}), 401
    if user.get("role") == ROLE_ADMIN:
        return None
    if resource_user_id and resource_user_id == user.get("id"):
        return None
    return jsonify({"ok": False, "error": "not permitted for this account"}), 403


# ── Installation ───────────────────────────────────────────────────────────────

def install(
    app,
    db_module,
    *,
    legacy_admin_token: str = "",
    require_https: bool | None = None,
    allowed_origins: tuple[str, ...] = (),
    idle_seconds: int | None = None,
) -> RateLimiter:
    """Wire the auth gate onto an app.  Returns the limiter for route use."""

    limiter = RateLimiter()
    if require_https is None:
        require_https = os.environ.get("FACEID_REQUIRE_HTTPS", "").strip() in ("1", "true", "yes")
    app.config["FACEID_REQUIRE_HTTPS"] = require_https
    app.config["FACEID_ALLOWED_ORIGINS"] = allowed_origins
    app.config["FACEID_LIMITER"] = limiter

    def _error(message: str, status: int):
        return jsonify({"ok": False, "error": message}), status

    def _authenticate() -> dict[str, Any] | None:
        """Identify the caller from the session cookie, else the legacy token."""
        raw = request.cookies.get(SESSION_COOKIE)
        if raw:
            user = db_module.get_session_user(raw, idle_seconds=idle_seconds)
            if user:
                return user
        # Transitional: the shared master token still authenticates as an
        # administrator so existing deployments keep working while the browser
        # migrates to cookies.  Removed at the end of the migration; see
        # FACEID_ALLOW_LEGACY_TOKEN.
        if legacy_admin_token:
            presented = _legacy_token_presented()
            if presented and hmac.compare_digest(presented, legacy_admin_token):
                return {
                    "id": None,
                    "name": "legacy-token",
                    "role": ROLE_ADMIN,
                    "legacy": True,
                    "session_id": None,
                }
        return None

    def _origin_is_trusted() -> bool:
        """Reject cross-site state changes.

        Cookie auth means the browser attaches credentials automatically, so a
        state-changing request needs a second signal that it came from our own
        page.  SameSite=Strict is the primary defense; this is the backstop for
        clients that ignore it.  A request with no Origin header at all is not a
        browser form post, so it is allowed through to the session check.
        """
        origin = request.headers.get("Origin")
        if not origin:
            return True
        origin = origin.rstrip("/")
        configured = app.config.get("FACEID_ALLOWED_ORIGINS") or ()
        if origin in configured:
            return True
        # Same-origin is always fine: this is the normal path once Flask serves
        # the built frontend itself.
        return origin == request.host_url.rstrip("/")

    @app.before_request
    def _enforce_policy():
        if request.method == "OPTIONS":
            return None

        policy = ENDPOINT_POLICY.get(request.endpoint, POLICY_ADMIN)

        if request.method in STATE_CHANGING and not _origin_is_trusted():
            app.logger.warning(
                "Blocked cross-origin %s %s from origin %s",
                request.method, request.path, request.headers.get("Origin"),
            )
            return _error("cross-origin request rejected", 403)

        if policy == POLICY_PUBLIC:
            return None

        user = _authenticate()
        if not user:
            app.logger.warning(
                "Unauthenticated %s %s from %s",
                request.method, request.path, request.remote_addr,
            )
            return _error("authentication required", 401)

        g.user = user

        if policy == POLICY_ADMIN and user.get("role") != ROLE_ADMIN:
            app.logger.warning(
                "Authorization denied: %s (%s) attempted %s %s",
                user.get("name"), user.get("role"), request.method, request.path,
            )
            _safe_log(db_module, "authz_denied", "fail",
                      detail=f"{request.method} {request.path}", user_id=user.get("id"))
            return _error("administrator access required", 403)

        return None

    return limiter


def _safe_log(db_module, stage: str, result: str, detail: str = "", user_id=None) -> None:
    """Audit writes must never take down the request that triggered them."""
    try:
        db_module.log_event(stage, result, detail=detail, user_id=user_id)
    except Exception:  # pragma: no cover - audit is best effort
        pass


def set_session_cookie(response, token: str, app, max_age: int):
    """Attach the session cookie with production-appropriate flags."""
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,                                   # unreadable from JavaScript
        secure=bool(app.config.get("FACEID_REQUIRE_HTTPS")),
        samesite="Strict",                               # primary CSRF defense
        path="/",
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="Strict")
    return response
