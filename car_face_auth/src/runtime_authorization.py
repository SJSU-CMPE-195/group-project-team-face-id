"""Bind authenticated principals to runtime sessions and final side effects."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Protocol


class AuthorizationRejected(RuntimeError):
    """The caller or bound session is no longer authorized."""

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


class AuthorizationStore(Protocol):
    def still_authorized(self, principal: Any) -> bool: ...

    def synchronized(self): ...

    def user_auth_version(self, user_id: str) -> int | None: ...

    def user_is_current(
        self,
        user_id: str,
        auth_version: int,
        *,
        require_face_access: bool = False,
    ) -> bool: ...

    def check_face_attempt(self, principal: Any) -> None: ...

    def record_face_result(
        self,
        principal: Any,
        matched: bool,
        limit: int,
    ) -> None: ...


class RuntimeAuthorization:
    """Keep policy checks small and independent from camera/actuator code."""

    def __init__(self) -> None:
        self._store: AuthorizationStore | None = None

    @property
    def required(self) -> bool:
        return self._store is not None

    def configure(self, store: AuthorizationStore) -> None:
        required_methods = (
            "still_authorized",
            "synchronized",
            "user_auth_version",
            "user_is_current",
        )
        if any(not callable(getattr(store, method, None)) for method in required_methods):
            raise TypeError("authorization store does not implement the runtime contract")
        self._store = store

    def bind(
        self,
        principal: Any | None,
        *,
        expected_user_id: str | None,
        require_admin: bool = False,
    ) -> dict[str, Any]:
        if self._store is None:
            if principal is not None:
                raise AuthorizationRejected(
                    "authorization policy must be configured before binding a principal"
                )
            return {
                "actor_device_id": None,
                "user_id": None,
                "auth_version": None,
                "actor_is_admin": False,
                "expected_user_id": expected_user_id,
                "expected_user_auth_version": None,
                "_authorization_principal": None,
            }

        if principal is None:
            raise AuthorizationRejected("authenticated authorization is required")
        device_id, user_id, auth_version, is_admin = self._principal_fields(principal)
        if not expected_user_id:
            raise AuthorizationRejected("an authorized user target is required")

        try:
            with self._store.synchronized():
                if not self._store.still_authorized(principal):
                    raise AuthorizationRejected("authorization is no longer valid")
                if require_admin and not is_admin:
                    raise AuthorizationRejected("administrator authorization is required")
                target_version = self._store.user_auth_version(expected_user_id)
                if target_version is None:
                    raise AuthorizationRejected("target user is no longer active")
        except AuthorizationRejected:
            raise
        except Exception as exc:
            raise AuthorizationRejected("authorization could not be verified") from exc

        return {
            "actor_device_id": device_id,
            "user_id": user_id,
            "auth_version": auth_version,
            "actor_is_admin": is_admin,
            "expected_user_id": expected_user_id,
            "expected_user_auth_version": target_version,
            "_authorization_principal": principal,
        }

    def require_owner(self, session: dict[str, Any], principal: Any | None) -> None:
        if self._store is None:
            return
        if principal is None:
            raise AuthorizationRejected("authenticated authorization is required")
        device_id, user_id, auth_version, _is_admin = self._principal_fields(principal)
        same_actor = (
            session.get("actor_device_id") == device_id
            and session.get("user_id") == user_id
        )
        immutable_success = session.get("state") in {"completed", "granted"}
        if (
            not same_actor
            or (
                session.get("auth_version") != auth_version
                and not immutable_success
            )
        ):
            raise AuthorizationRejected("session belongs to another authorization")
        try:
            if not self._store.still_authorized(principal):
                raise AuthorizationRejected("authorization is no longer valid")
        except AuthorizationRejected:
            raise
        except Exception as exc:
            raise AuthorizationRejected("authorization could not be verified") from exc

    def session_is_current(
        self,
        session: dict[str, Any],
        *,
        require_admin: bool = False,
        require_target: bool = False,
        require_face_access: bool = False,
    ) -> bool:
        if self._store is None:
            return True
        principal = session.get("_authorization_principal")
        if principal is None:
            return False
        try:
            device_id, user_id, auth_version, is_admin = self._principal_fields(
                principal
            )
            if (
                session.get("actor_device_id") != device_id
                or session.get("user_id") != user_id
                or session.get("auth_version") != auth_version
                or (require_admin and not is_admin)
                or not self._store.still_authorized(principal)
            ):
                return False
            if require_target:
                expected_user_id = session.get("expected_user_id")
                expected_version = session.get("expected_user_auth_version")
                if not expected_user_id or not isinstance(expected_version, int):
                    return False
                return bool(
                    self._store.user_is_current(
                        expected_user_id,
                        expected_version,
                        require_face_access=require_face_access,
                    )
                )
            return True
        except Exception:
            return False

    def synchronized(self):
        if self._store is None:
            return nullcontext()
        return self._store.synchronized()

    def check_face_attempt(self, session: dict[str, Any]) -> None:
        """Fail closed when the bound principal cannot attempt face auth."""

        store, principal = self._face_principal(session)
        callback = getattr(store, "check_face_attempt", None)
        if not callable(callback):
            raise AuthorizationRejected(
                "face lockout policy is unavailable",
                503,
            )
        try:
            callback(principal)
        except Exception as exc:
            raise self._verification_error(
                exc,
                "face attempt could not be verified",
            ) from exc

    def record_face_result(
        self,
        session: dict[str, Any],
        *,
        matched: bool,
        limit: int,
    ) -> None:
        """Persist one completed face decision for the bound principal."""

        store, principal = self._face_principal(session)
        callback = getattr(store, "record_face_result", None)
        if not callable(callback):
            raise AuthorizationRejected(
                "face lockout policy is unavailable",
                503,
            )
        try:
            callback(principal, matched, limit)
        except Exception as exc:
            raise self._verification_error(
                exc,
                "face result could not be recorded",
            ) from exc

    def _face_principal(
        self,
        session: dict[str, Any],
    ) -> tuple[AuthorizationStore, Any]:
        store = self._store
        if store is None:
            raise AuthorizationRejected(
                "face lockout requires an authorization policy",
                503,
            )
        principal = session.get("_authorization_principal")
        if principal is None or not self.session_is_current(
            session,
            require_target=True,
            require_face_access=True,
        ):
            raise AuthorizationRejected("authorization is no longer valid")
        return store, principal

    @staticmethod
    def _verification_error(
        error: Exception,
        fallback: str,
    ) -> AuthorizationRejected:
        status_code = getattr(error, "status_code", 503)
        if isinstance(status_code, bool) or not isinstance(status_code, int):
            status_code = 503
        message = str(error).strip() or fallback
        return AuthorizationRejected(message, status_code)

    @staticmethod
    def _principal_fields(principal: Any) -> tuple[str, str, int, bool]:
        device_id = getattr(principal, "device_id", None)
        user_id = getattr(principal, "user_id", None)
        auth_version = getattr(principal, "auth_version", None)
        is_admin = getattr(principal, "is_admin", None)
        if not isinstance(device_id, str) or not device_id:
            raise AuthorizationRejected("authorization device is invalid")
        if not isinstance(user_id, str) or not user_id:
            raise AuthorizationRejected("authorization user is invalid")
        if isinstance(auth_version, bool) or not isinstance(auth_version, int):
            raise AuthorizationRejected("authorization version is invalid")
        if not isinstance(is_admin, bool):
            raise AuthorizationRejected("authorization role is invalid")
        return device_id, user_id, auth_version, is_admin
