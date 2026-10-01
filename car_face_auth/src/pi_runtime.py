"""Single-process Pi camera, face-recognition, and ESP32 runtime.

The module deliberately keeps Raspberry Pi-only imports lazy.  The Device API
can therefore be imported and its non-hardware routes tested on a workstation.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterator
import importlib
import json
import os
import threading
import time
import uuid
from typing import Any

from .camera_capture import (
    CAMERA_RECOVERY_HINT,
    FRAME_STALE_SECONDS,
    CapturedFrame,
    SessionCameraCapture,
)
from .runtime_authorization import AuthorizationRejected, RuntimeAuthorization
from .runtime_actuation import RuntimeActuation
from .runtime_safety import (
    RuntimeSafetySettings,
    validate_runtime_safety_settings,
)


WINDOW_SIZE = 10
MIN_MATCHES = 6
SAMPLES_NEEDED = 10
DEFAULT_SCAN_TIMEOUT = 20
DEFAULT_ENROLL_TIMEOUT = 180
DEFAULT_ENROLL_SAMPLE_INTERVAL = 0.5
DEFAULT_CAMERA_CLOSE_TIMEOUT = 2.0
PREVIEW_MAX_WIDTH = 640
PREVIEW_JPEG_QUALITY = 75
SESSION_TTL_SECONDS = 300
ESP_KEYWORDS = ("CP210", "CH340", "CH341", "FTDI", "USB-SERIAL", "USB Serial", "Silicon Labs")


class RuntimeRequestError(RuntimeError):
    """A client request cannot be started with the current runtime state."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class RuntimeBusyError(RuntimeRequestError):
    """The single Pi camera is already owned by another session."""


class PiRuntime:
    """Own the one camera session and all Pi-side actuator state."""

    camera_source = "pi_camera"
    camera_label = "Pi camera"
    actuator_feedback = "unavailable"
    actuator_control_available = True
    simulated_actuators = False
    actuator_block_reason = "Actuator control is unavailable."
    liveness_available = False

    def __init__(self, db_api: Any, *, face_engine: Any | None = None):
        self._db = db_api
        self._face_engine = face_engine
        self._lock = threading.RLock()
        self._camera_io_lock = threading.Lock()
        self._inference_lock = threading.Lock()
        self._serial_lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._active_session_id: str | None = None
        self._modules: dict[str, Any] | None = None
        self._model: Any = None
        self._camera: Any = None
        self._camera_owner_id: str | None = None
        self._camera_capture: SessionCameraCapture | None = None
        self._camera_frame_id = 0
        self._camera_cleanup_thread: threading.Thread | None = None
        self._serial: Any = None
        self._serial_port: str | None = None
        self._hardware_error: str | None = None
        self._model_error: str | None = None
        self._camera_error: str | None = None
        self._serial_error: str | None = None
        self._settings_error: str | None = None
        self._authorization = RuntimeAuthorization()
        self._actuation = RuntimeActuation(
            db_api,
            self._send_command,
            self._log,
        )
        self._closed = False
        self._paused = False
        self._maintenance_drained = False
        self._maintenance_reason: str | None = None
        self._cleanup_timers: set[threading.Timer] = set()

    # ── Public status and session API ────────────────────────────────────────

    def configure_authorization(self, store: Any) -> None:
        """Require authenticated principals for all sessions created afterward."""

        with self._lock:
            if self._active_session_id:
                raise RuntimeRequestError(
                    "authorization cannot be configured while a session is active",
                    409,
                )
            self._authorization.configure(store)

    def status(self) -> dict[str, Any]:
        with self._lock:
            paused = self._paused
            maintenance_reason = self._maintenance_reason
        settings_ready = False if paused else self._refresh_safety_settings()
        actuation = self._actuation.snapshot()
        with self._lock:
            active = self._sessions.get(self._active_session_id or "")
            active_view = None
            if active:
                active_view = {
                    "session_id": active["id"],
                    "kind": active["kind"],
                    "state": active["state"],
                }
            modules_loaded = self._modules is not None
            camera_ready = self._modules is not None and self._model is not None and self._camera_error is None
            actuator_ready = bool(
                self.actuator_control_available and self._serial is not None
            )
            runtime_error = (
                self._settings_error
                or (
                    None
                    if self.actuator_control_available
                    else self.actuator_block_reason
                )
                or self._hardware_error
                or self._model_error
                or self._camera_error
                or self._serial_error
            )
            return {
                "ready": bool(
                    not self._closed
                    and not paused
                    and camera_ready
                    and actuator_ready
                    and settings_ready
                ),
                "hardware": "closed" if self._closed else ("maintenance" if paused else ("ready" if camera_ready and actuator_ready else (
                    "degraded" if camera_ready else (
                        "unavailable"
                        if (
                            not self.actuator_control_available
                            or self._hardware_error
                            or self._model_error
                            or self._camera_error
                            or self._serial_error
                        )
                        else "not_initialized"
                    )
                ))),
                "dependencies_loaded": modules_loaded,
                "model_loaded": self._model is not None,
                "camera_open": self._camera is not None,
                "camera_source": self.camera_source,
                "esp32_connected": bool(
                    self.actuator_control_available and self._serial is not None
                ),
                "serial_port": (
                    self._serial_port
                    if self.actuator_control_available
                    else None
                ),
                "settings_ready": settings_ready,
                "liveness_available": self.liveness_available,
                "actuator_feedback": self.actuator_feedback,
                "actuator_control_available": self.actuator_control_available,
                "simulated_actuators": self.simulated_actuators,
                "physical_state_confirmed": False,
                "ignitionOn": actuation.ignition_on,
                "ignition_authorized": actuation.unlock_owner is not None,
                "unlock_owner": actuation.unlock_owner,
                "active_session": active_view,
                "maintenance": paused,
                "maintenance_reason": maintenance_reason,
                "error": runtime_error,
            }

    def start_scan(
        self,
        purpose: str = "unlock",
        expected_user: str | None = None,
        *,
        authorization: Any | None = None,
    ) -> dict[str, Any]:
        session = self._start_scan_session(
            purpose,
            expected_user,
            source=self.camera_source,
            authorization=authorization,
        )
        self._spawn(session["id"], self._run_scan)
        return self._scan_view(session)

    def camera_status(
        self,
        *,
        authorization: Any | None = None,
    ) -> dict[str, Any]:
        """Return the latest host-camera session and live preview availability."""

        with self._lock:
            host_sessions = sorted(
                (
                    session
                    for session in self._sessions.values()
                    if session.get("source") == self.camera_source
                    and session.get("kind") in {"scan", "enroll"}
                ),
                key=lambda item: item["created_at"],
                reverse=True,
            )

        session = None
        for candidate in host_sessions:
            try:
                self._authorization.require_camera_viewer(candidate, authorization)
            except AuthorizationRejected:
                continue
            session = candidate
            break

        with self._lock:
            session_view = None
            if session and self._sessions.get(session["id"]) is session:
                view = (
                    self._scan_view(session)
                    if session["kind"] == "scan"
                    else self._enroll_view(session)
                )
                session_view = {"kind": session["kind"], **view}
            capture = self._camera_capture
            active_session_id = self._active_session_id
            frame_id = 0 if self._authorization.required else self._camera_frame_id

        frame = None
        if (
            session
            and capture
            and capture.session_id == session["id"]
            and capture.session_id == active_session_id
        ):
            frame = capture.snapshot()
            frame_id = max(frame_id, capture.frame_id)
        frame_available = bool(
            frame
            and frame.jpeg
            and self._camera_capture_is_active(capture.session_id, capture)
        )
        return {
            "camera_source": self.camera_source,
            "session": session_view,
            "frame_id": frame_id,
            "frame_available": frame_available,
        }

    def camera_frame(
        self,
        session_id: str,
        *,
        authorization: Any | None = None,
    ) -> bytes | None:
        """Return the fresh cached JPEG for the active host-camera session."""

        self.require_camera_viewer(session_id, authorization)
        capture = self._active_camera_capture(session_id)
        if not capture:
            return None
        frame = capture.snapshot()
        if (
            not frame
            or not frame.jpeg
            or not self._camera_capture_is_active(session_id, capture)
        ):
            return None
        return frame.jpeg

    def camera_stream(
        self,
        session_id: str,
        *,
        authorization: Any | None = None,
    ) -> Iterator[bytes] | None:
        """Stream fresh JPEGs without owning or controlling camera capture."""

        self.require_camera_viewer(session_id, authorization)
        capture = self._active_camera_capture(session_id)
        if not capture:
            return None

        def stream_frames() -> Iterator[bytes]:
            frame_id = -1
            last_jpeg_at = time.monotonic()
            while self._camera_capture_is_active(session_id, capture):
                if (
                    not self._session_authorization_is_current(session_id)
                    or not self._camera_viewer_is_current(session_id, authorization)
                ):
                    return
                frame = capture.wait_for_newer(frame_id, 1.0)
                if frame is None:
                    if capture.failure():
                        return
                    continue
                frame_id = frame.frame_id
                if (
                    capture.failure()
                    or time.monotonic() - frame.captured_at > FRAME_STALE_SECONDS
                ):
                    return
                if not frame.jpeg:
                    if time.monotonic() - last_jpeg_at > FRAME_STALE_SECONDS:
                        return
                    continue
                last_jpeg_at = time.monotonic()
                if not self._camera_capture_is_active(session_id, capture):
                    return
                if (
                    not self._session_authorization_is_current(session_id)
                    or not self._camera_viewer_is_current(session_id, authorization)
                ):
                    return
                yield self._multipart_frame(frame)

        return stream_frames()

    def start_client_scan(self, purpose: str = "unlock", expected_user: str | None = None) -> dict[str, Any]:
        raise RuntimeRequestError(
            "Unlock and ignition verification require the backend host camera.",
            409,
        )

    def _start_scan_session(
        self,
        purpose: str,
        expected_user: str | None,
        *,
        source: str,
        authorization: Any | None = None,
    ) -> dict[str, Any]:
        purpose = (purpose or "unlock").strip().lower()
        if purpose not in ("unlock", "ignition"):
            raise RuntimeRequestError("purpose must be unlock or ignition", 400)
        if not self.actuator_control_available:
            raise RuntimeRequestError(self.actuator_block_reason, 503)
        expected_user = (expected_user or "").strip() or None
        expected_user_id = None
        if self._authorization.required:
            principal_user_id = getattr(authorization, "user_id", None)
            if not isinstance(principal_user_id, str) or not principal_user_id:
                raise RuntimeRequestError("authenticated authorization is required", 403)
            principal_user = self._user_by_id(principal_user_id)
            if not principal_user or not self._face_access_allowed(principal_user):
                raise RuntimeRequestError("authorized user cannot use face access", 403)
            canonical_name = (principal_user.get("name") or "").strip()
            if not canonical_name:
                raise RuntimeRequestError("authorized user has no canonical name", 403)
            if expected_user and expected_user.casefold() != canonical_name.casefold():
                raise RuntimeRequestError(
                    "expected_user does not match the authorized user",
                    403,
                )
            expected_user = canonical_name
            expected_user_id = principal_user.get("id")
        actuation = self._actuation.snapshot()
        if purpose == "ignition":
            unlock_owner = actuation.unlock_owner
            unlock_owner_id = actuation.unlock_owner_id
            if not unlock_owner:
                raise RuntimeRequestError("a face-verified unlock is required before ignition", 409)
            if expected_user and expected_user.casefold() != unlock_owner.casefold():
                raise RuntimeRequestError("expected_user does not match the active unlock", 409)
            if self._authorization.required and expected_user_id != unlock_owner_id:
                raise RuntimeRequestError(
                    "authorized user does not own the active unlock",
                    403,
                )
            expected_user = unlock_owner
            expected_user_id = unlock_owner_id
            try:
                if self._db.get_status().get("lockState") == "locked":
                    raise RuntimeRequestError("device is locked", 409)
            except RuntimeRequestError:
                raise
            except Exception as exc:
                raise RuntimeRequestError(f"could not read device state: {exc}", 503) from exc

        try:
            authorization_values = self._authorization.bind(
                authorization,
                expected_user_id=expected_user_id,
            )
        except AuthorizationRejected as exc:
            raise RuntimeRequestError(str(exc), exc.status_code) from exc

        try:
            safety_settings = self._require_safety_settings()
            if safety_settings.fail_lockout:
                self._authorization.check_face_attempt(authorization_values)
        except AuthorizationRejected as exc:
            raise RuntimeRequestError(str(exc), exc.status_code) from exc

        session = self._new_session(
            "scan",
            {
                **authorization_values,
                "purpose": purpose,
                "source": source,
                "expected_user": expected_user,
                "authorization_generation": actuation.generation,
                "state": "starting" if source == self.camera_source else "scanning",
                "user": None,
                "score": None,
                "face_count": 0,
                "matches": 0,
                "history": None,
                "database": None,
                "face_mismatch_observed": False,
                "face_result_recorded": False,
                "message": (
                    f"{self.camera_label} scan starting."
                    if source == self.camera_source
                    else "Client camera scan is ready for frames."
                ),
                "window": {"matches": 0, "needed": MIN_MATCHES, "size": WINDOW_SIZE},
            },
        )
        return session

    def add_client_scan_sample(self, session_id: str, image_bytes: bytes) -> dict[str, Any]:
        raise RuntimeRequestError(
            "Verification frames must come from the backend host camera.",
            409,
        )

    def scan_status(self, session_id: str) -> dict[str, Any]:
        return self._get_view(session_id, "scan")

    def cancel_scan(self, session_id: str) -> dict[str, Any]:
        result = self._cancel(session_id, "scan")
        if result.get("source") == "client_camera":
            self._release_session(session_id)
        return result

    def start_enrollment(
        self,
        name: str,
        *,
        authorization: Any | None = None,
    ) -> dict[str, Any]:
        user = self._resolve_enrollment_user(name)
        authorization_values = self._bind_enrollment_authorization(
            authorization,
            user["id"],
        )
        self._require_safety_settings()
        session = self._new_session(
            "enroll",
            {
                **authorization_values,
                "name": user["name"],
                "source": self.camera_source,
                "state": "capturing",
                "count": 0,
                "message": f"{self.camera_label} enrollment starting.",
            },
        )
        self._spawn(session["id"], self._run_enrollment)
        return self._enroll_view(session)

    def start_client_enrollment(
        self,
        name: str,
        *,
        authorization: Any | None = None,
    ) -> dict[str, Any]:
        """Start an enrollment whose JPEG samples are uploaded by a client."""

        user = self._resolve_enrollment_user(name)
        authorization_values = self._bind_enrollment_authorization(
            authorization,
            user["id"],
        )
        self._require_safety_settings()
        timeout = self._bounded_seconds("PI_ENROLL_TIMEOUT_SECONDS", DEFAULT_ENROLL_TIMEOUT)
        session = self._new_session(
            "enroll",
            {
                **authorization_values,
                "name": user["name"],
                "source": "client_camera",
                "state": "capturing",
                "count": 0,
                "deadline": time.monotonic() + timeout,
                "embeddings": [],
                "face_count": 0,
                "message": "Client camera enrollment is ready for samples.",
            },
        )
        timer = threading.Timer(timeout, self._expire_client_enrollment, args=(session["id"],))
        timer.daemon = True
        with self._lock:
            session["timeout_timer"] = timer
            view = self._enroll_view(session)
        timer.start()
        return view

    def add_client_enrollment_sample(self, session_id: str, image_bytes: bytes) -> dict[str, Any]:
        """Decode one client JPEG on the Pi and accept exactly one face."""

        if not image_bytes:
            raise RuntimeRequestError("image is required", 400)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.get("kind") != "enroll":
                raise RuntimeRequestError("unknown enroll session", 404)
            if session.get("source") != "client_camera":
                raise RuntimeRequestError("session does not accept client camera samples", 409)
            if session.get("state") != "capturing":
                raise RuntimeRequestError("enrollment session is not capturing", 409)
            if time.monotonic() >= session["deadline"]:
                self._expire_client_enrollment(session_id)
                raise RuntimeRequestError("enrollment session timed out", 409)
            if session["cancel_event"].is_set():
                raise RuntimeRequestError("enrollment session was cancelled", 409)
            if len(session.get("embeddings", [])) >= SAMPLES_NEEDED:
                return self._enroll_view(session)

        try:
            face_engine = self._get_face_engine()
            model = self._ensure_model()
            frame = face_engine.decode_image_bytes(image_bytes)
            if frame is None:
                raise RuntimeRequestError("image could not be decoded", 400)
            with self._inference_lock:
                embedding, face_count = face_engine.extract_single_face_embedding(model, frame)
        except RuntimeRequestError:
            raise
        except Exception as exc:
            raise RuntimeRequestError(f"face sample could not be processed: {exc}", 503) from exc

        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.get("kind") != "enroll":
                raise RuntimeRequestError("unknown enroll session", 404)
            if session.get("source") != "client_camera" or session.get("state") != "capturing":
                raise RuntimeRequestError("enrollment session is not capturing", 409)
            if time.monotonic() >= session["deadline"]:
                self._expire_client_enrollment(session_id)
                raise RuntimeRequestError("enrollment session timed out", 409)
            session["face_count"] = int(face_count or 0)
            if embedding is not None and len(session["embeddings"]) < SAMPLES_NEEDED:
                session["embeddings"].append(embedding)
                session["count"] = len(session["embeddings"])
                session["message"] = f"Captured {session['count']}/{SAMPLES_NEEDED} client face samples."
            elif face_count == 0:
                session["message"] = "No face detected."
            else:
                session["message"] = "Multiple faces detected; show one face."
            session["updated_at"] = self._now_ms()
            return self._enroll_view(session)

    def finish_client_enrollment(self, session_id: str) -> dict[str, Any]:
        """Persist an uploaded enrollment into the Pi's canonical SQLite DB."""

        # Serialize persistence with cancel, timeout, reset, and duplicate finish.
        with self._authorization.synchronized(), self._actuation.synchronized(), self._lock:
            session = self._sessions.get(session_id)
            if not session or session.get("kind") != "enroll":
                raise RuntimeRequestError("unknown enroll session", 404)
            if session.get("source") != "client_camera":
                raise RuntimeRequestError("session does not accept client camera finish", 409)
            if session.get("state") == "completed":
                return self._enroll_view(session)
            if session.get("state") != "capturing":
                raise RuntimeRequestError("enrollment session is not capturing", 409)
            if self._closed or session["cancel_event"].is_set():
                raise RuntimeRequestError("enrollment session was cancelled", 409)
            if time.monotonic() >= session["deadline"]:
                self._expire_client_enrollment(session_id)
                raise RuntimeRequestError("enrollment session timed out", 409)
            embeddings = list(session.get("embeddings", []))
            if len(embeddings) < SAMPLES_NEEDED:
                raise RuntimeRequestError(
                    f"Need {SAMPLES_NEEDED} samples, have {len(embeddings)}",
                    400,
                )
            session.update(state="saving", message="Saving face template on device.", updated_at=self._now_ms())
            name = session["name"]

            try:
                self._safety_settings()
                if not self._authorization.session_is_current(
                    session,
                    require_admin=True,
                    require_target=True,
                ):
                    raise RuntimeError(
                        "Enrollment authorization expired before saving."
                    )
                face_engine = self._get_face_engine()
                save_result = face_engine.save_user_embedding(name, embeddings)
                if isinstance(save_result, dict) and not save_result.get("ok"):
                    raise RuntimeError(save_result.get("error") or "could not save face enrollment")
                self._finish(
                    session_id,
                    "completed",
                    count=SAMPLES_NEEDED,
                    recognition_available=True,
                    message=f"Enrollment completed for {name} from client camera.",
                )
                return self.enrollment_status(session_id)
            except Exception as exc:
                self._finish(session_id, "error", message=str(exc))
                return self.enrollment_status(session_id)
            finally:
                self._release_session(session_id)

    def _expire_client_enrollment(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.get("kind") != "enroll":
                return
            if session.get("source") != "client_camera":
                return
            if session["state"] in self._final_states("enroll"):
                return
            session["cancel_event"].set()
            self._finish(session_id, "timeout", message="Enrollment timed out.")
            self._release_session(session_id)

    def _user_by_id(self, user_id: str) -> dict[str, Any] | None:
        try:
            getter = getattr(self._db, "get_user_by_id", None)
            if callable(getter):
                return getter(user_id)
            return next(
                (row for row in self._db.get_all_users() if row.get("id") == user_id),
                None,
            )
        except Exception as exc:
            raise RuntimeRequestError(f"could not read users: {exc}", 503) from exc

    @staticmethod
    def _face_access_allowed(user: dict[str, Any]) -> bool:
        value = user.get("face_access", user.get("faceAccess", 1))
        return bool(value)

    def _resolve_enrollment_user(self, name: str) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            raise RuntimeRequestError("name is required", 400)
        try:
            users = [row for row in self._db.get_all_users() if (row.get("name") or "").strip().casefold() == name.casefold()]
        except Exception as exc:
            raise RuntimeRequestError(f"could not read users: {exc}", 503) from exc
        if not users:
            raise RuntimeRequestError("user must be created before enrollment", 404)
        if len(users) != 1:
            raise RuntimeRequestError("active user name is ambiguous", 409)
        return users[0]

    def _bind_enrollment_authorization(
        self,
        authorization: Any | None,
        expected_user_id: str,
    ) -> dict[str, Any]:
        try:
            return self._authorization.bind(
                authorization,
                expected_user_id=expected_user_id,
                require_admin=True,
            )
        except AuthorizationRejected as exc:
            raise RuntimeRequestError(str(exc), 403) from exc

    def require_session_owner(
        self,
        session_id: str,
        authorization: Any | None,
    ) -> None:
        """Reject cross-device, stale, or revoked access to a runtime session."""

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise RuntimeRequestError("unknown session", 404)
        try:
            self._authorization.require_owner(session, authorization)
        except AuthorizationRejected as exc:
            raise RuntimeRequestError(str(exc), 403) from exc

    def require_camera_viewer(
        self,
        session_id: str,
        authorization: Any | None,
    ) -> None:
        """Authorize read-only camera access without granting session control."""

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise RuntimeRequestError("unknown session", 404)
        try:
            self._authorization.require_camera_viewer(session, authorization)
        except AuthorizationRejected as exc:
            raise RuntimeRequestError(str(exc), 403) from exc

    def _camera_viewer_is_current(
        self,
        session_id: str,
        authorization: Any | None,
    ) -> bool:
        try:
            self.require_camera_viewer(session_id, authorization)
            return True
        except RuntimeRequestError:
            return False

    def _session_authorization_is_current(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False
            kind = session.get("kind")
        return self._authorization.session_is_current(
            session,
            require_admin=kind == "enroll",
            require_target=self._authorization.required,
            require_face_access=kind == "scan",
        )

    def _check_face_attempt(self, session_id: str) -> None:
        session = self._session(session_id)
        if not session:
            raise RuntimeError("scan session is no longer active")
        try:
            self._authorization.check_face_attempt(session)
        except AuthorizationRejected as exc:
            raise RuntimeError(str(exc)) from exc

    def _record_face_result(
        self,
        session_id: str,
        *,
        matched: bool,
        limit: int,
    ) -> None:
        session = self._session(session_id)
        if not session:
            raise RuntimeError("scan session is no longer active")
        if session.get("face_result_recorded"):
            return
        try:
            self._authorization.record_face_result(
                session,
                matched=matched,
                limit=limit,
            )
        except AuthorizationRejected as exc:
            raise RuntimeError(str(exc)) from exc
        with self._lock:
            current = self._sessions.get(session_id)
            if current is not session:
                raise RuntimeError("scan session is no longer active")
            current["face_result_recorded"] = True

    def cancel_device_sessions(self, device_id: str) -> int:
        """Cancel camera work for a revoked device without changing ignition."""

        cancelled = 0
        client_session_ids = []
        with self._actuation.synchronized(), self._lock:
            for session in self._sessions.values():
                if (
                    session.get("actor_device_id") == device_id
                    and session["state"] not in self._final_states(session["kind"])
                ):
                    session["cancel_event"].set()
                    session.update(
                        state="cancelled",
                        message="Session authorization was revoked.",
                        updated_at=self._now_ms(),
                    )
                    self._stop_camera_capture_now_locked(session["id"])
                    if session.get("source") == "client_camera":
                        client_session_ids.append(session["id"])
                    cancelled += 1
        for session_id in client_session_ids:
            self._release_session(session_id)
        return cancelled

    def enrollment_status(self, session_id: str) -> dict[str, Any]:
        return self._get_view(session_id, "enroll")

    def cancel_enrollment(self, session_id: str) -> dict[str, Any]:
        result = self._cancel(session_id, "enroll")
        if result.get("source") == "client_camera":
            self._release_session(session_id)
        return result

    # ── Actuator lifecycle ───────────────────────────────────────────────────

    def unlock(self, reason: str = "manual_ui") -> dict[str, Any]:
        raise RuntimeRequestError(
            "Direct unlock is disabled; start a backend host camera scan.",
            409,
        )

    def set_ignition(
        self,
        running: bool,
        reason: str = "manual_ui",
        *,
        ignition_stop_seconds: int | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            closed = self._closed
            paused = self._paused
        if paused:
            return {
                "ok": False,
                "error": "Pi runtime is in maintenance",
                "command_sent": False,
                "physical_state_confirmed": False,
            }
        if running and ignition_stop_seconds is None:
            try:
                ignition_stop_seconds = (
                    self._safety_settings().ignition_stop_seconds
                )
            except RuntimeError as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "command_sent": False,
                    "physical_state_confirmed": False,
                }
        return self._actuation.set_ignition(
            running,
            reason,
            ignition_stop_seconds=ignition_stop_seconds or 0,
            closed=closed,
        )

    def force_lock(self, reason: str = "manual_ui") -> dict[str, Any]:
        """Cancel work and report lock only after both commands are sent."""
        with self._lock:
            if self._paused:
                return {
                    "ok": False,
                    "error": "Pi runtime is in maintenance",
                    "command_sent": False,
                    "physical_state_confirmed": False,
                }
        self._cancel_all_sessions()
        result = self._actuation.force_lock(reason)
        # A stuck camera teardown must not delay STOP/LOCK command dispatch.
        camera_closed = self._close_camera()
        if not result.get("ok"):
            return result
        if not camera_closed:
            return {
                **result,
                "ok": False,
                "error": self._camera_error or "camera teardown did not complete",
                "locked": True,
            }
        return result

    def quiesce(
        self,
        reason: str = "developer_reset",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Reversibly stop runtime work before simulated power or reset."""

        reason = (reason or "maintenance").strip() or "maintenance"
        timeout = max(0.0, float(timeout))
        deadline = time.monotonic() + timeout
        with self._lock:
            if self._closed:
                raise RuntimeError("Pi runtime is shut down")
            if self._paused and self._maintenance_drained:
                return {
                    "ok": True,
                    "maintenance": True,
                    "reason": self._maintenance_reason,
                }
            self._paused = True
            self._maintenance_drained = False
            self._maintenance_reason = reason
            session_timers = [
                session.get("timeout_timer")
                for session in self._sessions.values()
                if session.get("timeout_timer") is not None
            ]
            worker_threads = [
                session.get("thread")
                for session in self._sessions.values()
                if session.get("thread") is not None
            ]
        self._cancel_all_sessions()
        for timer in session_timers:
            timer.cancel()

        errors: list[str] = []
        lock_result: dict[str, Any] = {}
        try:
            lock_result = self._actuation.quiesce(
                reason,
                max(0.0, deadline - time.monotonic()),
            )
        except Exception as exc:
            errors.append(str(exc) or exc.__class__.__name__)
        if not self._close_camera():
            errors.append(
                self._camera_error or "camera teardown did not complete"
            )
        self._close_serial()
        try:
            self._join_maintenance_threads(worker_threads, deadline)
        except RuntimeError as exc:
            errors.append(str(exc))

        with self._lock:
            cleanup_thread = self._camera_cleanup_thread
        try:
            self._join_maintenance_threads([cleanup_thread], deadline)
        except RuntimeError as exc:
            errors.append(str(exc))

        with self._lock:
            cleanup_timers = list(self._cleanup_timers)
            for session in self._sessions.values():
                timer = session.get("timeout_timer")
                if timer is not None:
                    cleanup_timers.append(timer)
        for timer in cleanup_timers:
            timer.cancel()
        try:
            self._join_maintenance_threads(cleanup_timers, deadline)
        except RuntimeError as exc:
            errors.append(str(exc))

        with self._lock:
            live_workers = [
                session.get("thread")
                for session in self._sessions.values()
                if session.get("thread") is not None
                and session["thread"].is_alive()
            ]
            cleanup_alive = bool(
                self._camera_cleanup_thread
                and self._camera_cleanup_thread.is_alive()
            )
            if live_workers or cleanup_alive or self._camera is not None:
                errors.append("runtime work did not stop before timeout")
            self._active_session_id = None
            self._maintenance_drained = not errors
        if time.monotonic() > deadline:
            errors.append("runtime maintenance did not finish before timeout")
            with self._lock:
                self._maintenance_drained = False
        if errors:
            raise RuntimeError("; ".join(dict.fromkeys(errors)))
        return {
            **lock_result,
            "maintenance": True,
            "reason": reason,
        }

    def resume_after_maintenance(self) -> None:
        """Reload database-backed state and accept new runtime work."""

        with self._lock:
            if self._closed:
                raise RuntimeError("Pi runtime is shut down")
            if not self._paused:
                return
            if not self._maintenance_drained:
                raise RuntimeError("runtime maintenance has not drained")

        self._safety_settings()
        self._load_authorized_database(self._get_face_engine())
        self._actuation.resume_after_maintenance()
        with self._lock:
            self._sessions.clear()
            self._cleanup_timers.clear()
            self._active_session_id = None
            self._camera_cleanup_thread = None
            self._camera_error = None
            self._maintenance_reason = None
            self._maintenance_drained = False
            self._paused = False

    def close(self) -> None:
        """Best-effort process shutdown: stop, lock, then release hardware."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            had_hardware = self._serial is not None or self._modules is not None
        self._cancel_all_sessions()
        self._actuation.shutdown(had_hardware)
        self._close_camera()
        self._close_serial()

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Serialize authorization mutations with the final grant decision."""
        with self._actuation.synchronized():
            user = self._db.get_user_by_id(user_id)
            result = self._db.delete_user(user_id)
            if result.get("ok") and user:
                self._invalidate_authorization(user.get("name"))
            return result

    def set_user_access(self, user_id: str, allowed: bool) -> dict[str, Any]:
        """Apply access changes atomically with respect to scan actuation."""
        with self._actuation.synchronized():
            user = self._db.get_user_by_id(user_id)
            result = self._db.set_user_access(user_id, allowed)
            if result.get("ok") and not allowed and user:
                self._invalidate_authorization(user.get("name"))
            return result

    # ── Lazy Pi hardware ─────────────────────────────────────────────────────

    def _get_face_engine(self) -> Any:
        if self._face_engine is not None:
            return self._face_engine
        return importlib.import_module("car_face_auth.src.face_engine")

    def _load_modules(self) -> dict[str, Any]:
        with self._lock:
            if self._modules is not None:
                return self._modules
        try:
            modules = {
                "cv2": importlib.import_module("cv2"),
                "FaceAnalysis": importlib.import_module("insightface.app").FaceAnalysis,
                "Picamera2": importlib.import_module("picamera2").Picamera2,
                "serial": importlib.import_module("serial"),
                "list_ports": importlib.import_module("serial.tools.list_ports"),
            }
        except Exception as exc:
            message = f"Pi dependencies unavailable: {exc}"
            self._record_error(message, "hardware")
            raise RuntimeError(message) from exc
        with self._lock:
            self._modules = modules
            self._hardware_error = None
        return modules

    def _ensure_model(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
        try:
            face_analysis = self._load_modules()["FaceAnalysis"]
            model = face_analysis(
                name="buffalo_s",
                allowed_modules=["detection", "recognition"],
                providers=["CPUExecutionProvider"],
            )
            model.prepare(ctx_id=-1, det_size=(320, 320))
        except Exception as exc:
            message = f"InsightFace model unavailable: {exc}"
            self._record_error(message, "model")
            raise RuntimeError(message) from exc
        with self._lock:
            self._model = model
            self._model_error = None
        return model

    def _open_camera(self, owner_id: str | None = None) -> Any:
        with self._lock:
            if self._camera is not None:
                return self._camera
            if owner_id:
                session = self._sessions.get(owner_id)
                if not session or session["cancel_event"].is_set():
                    raise RuntimeError("camera session cancelled")
        camera = None
        try:
            picamera = self._load_modules()["Picamera2"]
            camera = picamera()
            camera.configure(camera.create_preview_configuration(main={"format": "RGB888", "size": (640, 480)}))
            camera.start()
        except Exception as exc:
            if camera is not None:
                for method in ("stop", "close"):
                    try:
                        callback = getattr(camera, method, None)
                        if callback:
                            callback()
                    except Exception:
                        pass
            message = f"Pi camera unavailable: {exc}. {CAMERA_RECOVERY_HINT}"
            self._record_error(message, "camera")
            raise RuntimeError(message) from exc
        with self._lock:
            self._camera = camera
            self._camera_owner_id = owner_id
            self._camera_error = None
        return camera

    def _find_esp_port(self, list_ports_module: Any) -> str | None:
        configured = (os.environ.get("ESP32_SERIAL_PORT") or "").strip()
        if configured:
            return configured
        ports = list(list_ports_module.comports())
        for port in ports:
            desc = (getattr(port, "description", "") or "") + (getattr(port, "manufacturer", "") or "")
            if any(keyword.lower() in desc.lower() for keyword in ESP_KEYWORDS):
                return port.device
        return None

    def _ensure_serial(self) -> Any:
        with self._serial_lock:
            if self._serial is not None and getattr(self._serial, "is_open", True):
                return self._serial
            try:
                serial_module = self._load_modules()["serial"]
                port = self._find_esp_port(self._load_modules()["list_ports"])
                if not port:
                    raise RuntimeError("ESP32 serial device not found")
                connection = serial_module.Serial(port, 115200, timeout=2)
                time.sleep(1)
                reset = getattr(connection, "reset_input_buffer", None)
                if reset:
                    reset()
            except Exception as exc:
                message = f"ESP32 unavailable: {exc}"
                self._record_error(message, "serial")
                raise RuntimeError(message) from exc
            with self._lock:
                self._serial = connection
                self._serial_port = port
                self._serial_error = None
            return connection

    def _send_command(self, command: str, connect: bool = True) -> bool:
        if not self.actuator_control_available:
            self._record_error(self.actuator_block_reason, "serial")
            return False
        if command not in {"UNLOCK", "LOCK", "START", "STOP"}:
            self._record_error("Unknown actuator command", "serial")
            return False
        try:
            connection = self._ensure_serial() if connect else self._serial
            if connection is None:
                raise RuntimeError("ESP32 serial device is not connected")
            payload = (command + "\n").encode("ascii")
            with self._serial_lock:
                if connection.write(payload) != len(payload):
                    raise RuntimeError("incomplete ESP32 serial write")
                connection.flush()
            # Serial delivery does not confirm motor movement or lock position.
            with self._lock:
                self._serial_error = None
            return True
        except Exception as exc:
            self._record_error(f"ESP32 command {command} failed: {exc}", "serial")
            self._close_serial()
            return False

    def _close_serial(self) -> None:
        with self._serial_lock:
            connection = self._serial
            self._serial = None
            self._serial_port = None
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass

    # ── Background workers ───────────────────────────────────────────────────

    def _run_scan(self, session_id: str) -> None:
        session = self._session(session_id)
        if not session:
            return
        cancel_event = session["cancel_event"]
        history: deque[tuple[bool, str | None]] = deque(maxlen=WINDOW_SIZE)
        timeout = self._bounded_seconds("PI_SCAN_TIMEOUT_SECONDS", DEFAULT_SCAN_TIMEOUT)
        try:
            safety_settings = self._safety_settings()
            if not self._session_authorization_is_current(session_id):
                self._finish(
                    session_id,
                    "denied",
                    message="Authorization expired before scanning.",
                )
                return
            if safety_settings.fail_lockout:
                self._check_face_attempt(session_id)
            self._update(
                session_id,
                state="scanning",
                message=f"{self.camera_label} is scanning.",
            )
            face_engine = self._get_face_engine()
            if cancel_event.is_set():
                return
            model = self._ensure_model()
            if cancel_event.is_set():
                return
            camera = self._open_camera(session_id)
            if cancel_event.is_set():
                return
            capture = self._start_camera_capture(session_id, camera)
            frame_id = capture.frame_id
            database = self._load_authorized_database(face_engine)
            if not database:
                raise RuntimeError("No enrolled face with access enabled")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    return
                if not self._session_authorization_is_current(session_id):
                    self._finish(
                        session_id,
                        "denied",
                        message="Authorization was revoked during scanning.",
                    )
                    return
                captured = self._wait_for_camera_frame(
                    capture,
                    frame_id,
                    deadline,
                    cancel_event,
                )
                if captured is None:
                    continue
                frame_id = captured.frame_id
                result = face_engine.analyze_frame(model, captured.bgr, database)
                if not self._camera_capture_can_continue(
                    capture,
                    deadline,
                    cancel_event,
                ):
                    if cancel_event.is_set():
                        return
                    break
                matched = bool(result.get("matched") and result.get("user"))
                user = result.get("user") if matched else None
                history.append((matched, user))
                candidate, count = self._window_candidate(history)
                face_count = int(result.get("face_count") or 0)
                expected_user = session.get("expected_user")
                mismatch_observed = bool(
                    face_count == 1
                    and (
                        not matched
                        or (
                            expected_user
                            and isinstance(user, str)
                            and user.casefold() != expected_user.casefold()
                        )
                    )
                )
                self._update(
                    session_id,
                    user=result.get("user"),
                    score=result.get("score"),
                    face_count=face_count,
                    matches=count,
                    face_mismatch_observed=(
                        session.get("face_mismatch_observed", False)
                        or mismatch_observed
                    ),
                    window={"matches": count, "needed": MIN_MATCHES, "size": WINDOW_SIZE},
                    message=self._scan_message(face_count, result, candidate, count),
                )
                if count >= MIN_MATCHES:
                    if not self._camera_capture_can_continue(
                        capture,
                        deadline,
                        cancel_event,
                    ):
                        if cancel_event.is_set():
                            return
                        break
                    self._grant_scan(
                        session_id,
                        candidate,
                        result.get("score"),
                        cancel_event,
                        capture,
                        deadline,
                    )
                    return
            if not cancel_event.is_set():
                session = self._session(session_id)
                if (
                    safety_settings.fail_lockout
                    and session
                    and session.get("face_mismatch_observed")
                ):
                    self._record_face_result(
                        session_id,
                        matched=False,
                        limit=safety_settings.lockout_after,
                    )
                self._finish(
                    session_id,
                    "timeout",
                    message=f"Scan timed out after {timeout} seconds.",
                )
        except Exception as exc:
            if not cancel_event.is_set():
                self._finish(session_id, "error", message=str(exc))
        finally:
            if self._close_camera(session_id):
                self._release_session(session_id)
            else:
                self._defer_camera_cleanup(session_id)

    def _run_enrollment(self, session_id: str) -> None:
        session = self._session(session_id)
        if not session:
            return
        cancel_event = session["cancel_event"]
        embeddings: list[Any] = []
        timeout = self._bounded_seconds("PI_ENROLL_TIMEOUT_SECONDS", DEFAULT_ENROLL_TIMEOUT)
        try:
            self._safety_settings()
            if not self._session_authorization_is_current(session_id):
                self._finish(
                    session_id,
                    "error",
                    message="Enrollment authorization expired before capture.",
                )
                return
            self._update(
                session_id,
                state="capturing",
                message=f"{self.camera_label} enrollment is capturing samples.",
            )
            face_engine = self._get_face_engine()
            if cancel_event.is_set():
                return
            model = self._ensure_model()
            if cancel_event.is_set():
                return
            camera = self._open_camera(session_id)
            if cancel_event.is_set():
                return
            capture = self._start_camera_capture(session_id, camera)
            frame_id = capture.frame_id
            deadline = time.monotonic() + timeout
            while len(embeddings) < SAMPLES_NEEDED and time.monotonic() < deadline:
                if cancel_event.is_set():
                    return
                if not self._session_authorization_is_current(session_id):
                    self._finish(
                        session_id,
                        "error",
                        message="Enrollment authorization was revoked during capture.",
                    )
                    return
                captured = self._wait_for_camera_frame(
                    capture,
                    frame_id,
                    deadline,
                    cancel_event,
                )
                if captured is None:
                    continue
                frame_id = captured.frame_id
                embedding, face_count = face_engine.extract_single_face_embedding(
                    model,
                    captured.bgr,
                )
                if not self._camera_capture_can_continue(
                    capture,
                    deadline,
                    cancel_event,
                ):
                    if cancel_event.is_set():
                        return
                    break
                if embedding is not None:
                    embeddings.append(embedding)
                    self._update(
                        session_id,
                        count=len(embeddings),
                        message=f"Captured {len(embeddings)}/{SAMPLES_NEEDED} face samples.",
                    )
                    if len(embeddings) < SAMPLES_NEEDED:
                        cancel_event.wait(self._enroll_sample_interval())
                else:
                    message = "No face detected." if face_count == 0 else "Multiple faces detected; show one face."
                    self._update(session_id, message=message)
            if cancel_event.is_set():
                return
            if len(embeddings) < SAMPLES_NEEDED:
                self._finish(session_id, "timeout", message=f"Enrollment timed out after {timeout} seconds.")
                return
            if not self._camera_capture_can_continue(
                capture,
                deadline,
                cancel_event,
            ):
                if not cancel_event.is_set():
                    self._finish(
                        session_id,
                        "timeout",
                        message=f"Enrollment timed out after {timeout} seconds.",
                    )
                return
            with self._authorization.synchronized(), self._actuation.synchronized():
                if cancel_event.is_set():
                    return
                if not self._camera_capture_can_continue(
                    capture,
                    deadline,
                    cancel_event,
                ):
                    if not cancel_event.is_set():
                        self._finish(
                            session_id,
                            "timeout",
                            message=(
                                f"Enrollment timed out after {timeout} seconds."
                            ),
                        )
                    return
                name = session["name"]
                self._safety_settings()
                if not self._authorization.session_is_current(
                    session,
                    require_admin=True,
                    require_target=True,
                ):
                    self._finish(
                        session_id,
                        "error",
                        message="Enrollment authorization expired before saving.",
                    )
                    return
                save_result = face_engine.save_user_embedding(name, embeddings)
                if isinstance(save_result, dict) and not save_result.get("ok"):
                    raise RuntimeError(save_result.get("error") or "could not save face enrollment")
                self._finish(
                    session_id,
                    "completed",
                    count=SAMPLES_NEEDED,
                    recognition_available=True,
                    message=f"Enrollment completed for {name}.",
                )
        except Exception as exc:
            if not cancel_event.is_set():
                self._finish(session_id, "error", message=str(exc))
        finally:
            if self._close_camera(session_id):
                self._release_session(session_id)
            else:
                self._defer_camera_cleanup(session_id)

    def _grant_scan(
        self,
        session_id: str,
        candidate: str | None,
        score: Any,
        cancel_event: threading.Event,
        capture: SessionCameraCapture,
        deadline: float,
    ) -> None:
        session = self._session(session_id)
        if not session or cancel_event.is_set():
            return
        if session.get("source") != self.camera_source:
            self._finish(
                session_id,
                "denied",
                score=score,
                message="Unlock and ignition require the backend host camera.",
            )
            return
        if not candidate:
            self._finish(
                session_id,
                "denied",
                score=score,
                message="No authorized user matched the rolling window.",
            )
            return

        with (
            self._authorization.synchronized(),
            self._actuation.synchronized(),
        ):
            session = self._session(session_id)
            with self._lock:
                shutting_down = self._closed
            if not session or cancel_event.is_set() or shutting_down:
                return
            try:
                authorized_rows = [
                    row
                    for row in self._db.get_all_users()
                    if row.get("name") == candidate
                    and self._face_access_allowed(row)
                ]
                if len(authorized_rows) != 1:
                    self._finish(
                        session_id,
                        "denied",
                        user=candidate,
                        score=score,
                        message=(
                            "Access was revoked before the scan completed."
                        ),
                    )
                    return
            except Exception as exc:
                self._finish(
                    session_id,
                    "error",
                    message=f"could not confirm face access: {exc}",
                )
                return

            matched_user_id = authorized_rows[0].get("id")
            try:
                safety_settings = self._safety_settings()
                if safety_settings.fail_lockout:
                    self._check_face_attempt(session_id)
            except RuntimeError as exc:
                self._finish(session_id, "error", message=str(exc))
                return

            if self._authorization.required and (
                not matched_user_id
                or matched_user_id != session.get("user_id")
                or matched_user_id != session.get("expected_user_id")
            ):
                if safety_settings.fail_lockout:
                    try:
                        self._record_face_result(
                            session_id,
                            matched=False,
                            limit=safety_settings.lockout_after,
                        )
                    except RuntimeError as exc:
                        self._finish(session_id, "error", message=str(exc))
                        return
                self._finish(
                    session_id,
                    "denied",
                    user=candidate,
                    score=score,
                    message="Face did not match the authorized user.",
                )
                return

            if session["purpose"] == "ignition":
                self._grant_ignition(
                    session,
                    candidate,
                    matched_user_id,
                    score,
                    cancel_event,
                    capture,
                    deadline,
                    safety_settings,
                )
                return
            self._grant_unlock(
                session,
                candidate,
                matched_user_id,
                score,
                cancel_event,
                capture,
                deadline,
                safety_settings,
            )

    def _grant_ignition(
        self,
        session: dict[str, Any],
        candidate: str,
        matched_user_id: str | None,
        score: Any,
        cancel_event: threading.Event,
        capture: SessionCameraCapture,
        deadline: float,
        safety_settings: RuntimeSafetySettings,
    ) -> None:
        session_id = session["id"]
        expected_user = session.get("expected_user")
        actuation = self._actuation.snapshot()
        authorization_is_current = (
            actuation.unlock_owner is not None
            and expected_user is not None
            and candidate.casefold()
            == expected_user.casefold()
            == actuation.unlock_owner.casefold()
            and (
                not self._authorization.required
                or matched_user_id
                == session.get("expected_user_id")
                == actuation.unlock_owner_id
            )
            and session.get("authorization_generation")
            == actuation.generation
        )
        if not authorization_is_current:
            self._finish(
                session_id,
                "denied",
                user=candidate,
                score=score,
                message=(
                    "Ignition denied: face did not match the unlocked driver."
                ),
            )
            return
        try:
            if self._db.get_status().get("lockState") == "locked":
                self._finish(
                    session_id,
                    "denied",
                    user=candidate,
                    score=score,
                    message="Ignition denied because the device is locked.",
                )
                return
        except Exception as exc:
            self._finish(
                session_id,
                "error",
                message=f"could not read device state: {exc}",
            )
            return
        if cancel_event.is_set():
            return
        if not self._camera_capture_can_continue(
            capture,
            deadline,
            cancel_event,
        ):
            self._finish(
                session_id,
                "timeout",
                user=candidate,
                score=score,
                message="Ignition scan expired before actuation.",
            )
            return
        if not self._authorization.session_is_current(
            session,
            require_target=True,
            require_face_access=True,
        ):
            self._finish(
                session_id,
                "denied",
                user=candidate,
                score=score,
                message="Authorization was revoked before ignition.",
            )
            return
        if not self._record_successful_face_result(
            session_id,
            safety_settings,
        ):
            return

        result = self._actuation.set_ignition(
            True,
            f"scan:{session_id}",
            ignition_stop_seconds=safety_settings.ignition_stop_seconds,
            closed=False,
            expected_generation=actuation.generation,
        )
        if not result.get("ok"):
            self._finish(
                session_id,
                "error",
                user=candidate,
                score=score,
                command_sent=bool(result.get("command_sent")),
                physical_state_confirmed=False,
                message=result.get("error", "Could not start ignition."),
            )
            return
        self._finish(
            session_id,
            "granted",
            user=candidate,
            score=score,
            matches=MIN_MATCHES,
            command_sent=True,
            physical_state_confirmed=False,
            message=(
                "Ignition command sent for the authorized driver; "
                "physical state is unverified."
            ),
        )

    def _grant_unlock(
        self,
        session: dict[str, Any],
        candidate: str,
        matched_user_id: str | None,
        score: Any,
        cancel_event: threading.Event,
        capture: SessionCameraCapture,
        deadline: float,
        safety_settings: RuntimeSafetySettings,
    ) -> None:
        session_id = session["id"]
        if cancel_event.is_set():
            return
        if not self._camera_capture_can_continue(
            capture,
            deadline,
            cancel_event,
        ):
            self._finish(
                session_id,
                "timeout",
                user=candidate,
                score=score,
                message="Unlock scan expired before actuation.",
            )
            return
        if not self._authorization.session_is_current(
            session,
            require_target=True,
            require_face_access=True,
        ):
            self._finish(
                session_id,
                "denied",
                user=candidate,
                score=score,
                message="Authorization was revoked before unlock.",
            )
            return
        generation = session.get("authorization_generation")
        if generation != self._actuation.snapshot().generation:
            self._finish(
                session_id,
                "denied",
                user=candidate,
                score=score,
                message="Lock state changed before unlock.",
            )
            return
        if not self._record_successful_face_result(
            session_id,
            safety_settings,
        ):
            return

        result = self._actuation.unlock(
            candidate,
            matched_user_id,
            f"scan:{session_id}",
            auto_relock_seconds=safety_settings.auto_relock_seconds,
            expected_generation=generation,
        )
        if not result.get("ok"):
            self._finish(
                session_id,
                "error",
                user=candidate,
                score=score,
                command_sent=bool(result.get("command_sent")),
                unlock_command_sent=bool(
                    result.get("unlock_command_sent")
                ),
                compensating_lock_command_sent=bool(
                    result.get("compensating_lock_command_sent")
                ),
                auto_relock_armed=bool(
                    result.get("auto_relock_armed")
                ),
                physical_state_confirmed=False,
                message=result.get("error", "Could not unlock."),
            )
            return
        self._finish(
            session_id,
            "granted",
            user=candidate,
            score=score,
            matches=MIN_MATCHES,
            command_sent=True,
            physical_state_confirmed=False,
            message=(
                f"{self.camera_label} authorization passed and the unlock "
                "command was sent; physical lock position is unverified."
            ),
        )

    def _record_successful_face_result(
        self,
        session_id: str,
        safety_settings: RuntimeSafetySettings,
    ) -> bool:
        if not safety_settings.fail_lockout:
            return True
        try:
            self._record_face_result(
                session_id,
                matched=True,
                limit=safety_settings.lockout_after,
            )
            return True
        except RuntimeError as exc:
            self._finish(session_id, "error", message=str(exc))
            return False

    def _expire_client_scan(self, session_id: str, timeout: float) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                not session
                or session.get("kind") != "scan"
                or session.get("source") != "client_camera"
                or session.get("state") in self._final_states("scan")
            ):
                return
        self._finish(session_id, "timeout", message=f"Scan timed out after {timeout:g} seconds.")
        self._release_session(session_id)

    # ── Session bookkeeping ──────────────────────────────────────────────────

    def _new_session(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeRequestError("Pi runtime is shutting down", 503)
            if self._paused:
                raise RuntimeRequestError("Pi runtime is in maintenance", 503)
            self._prune_sessions()
            if self._active_session_id and self._active_session_id in self._sessions:
                raise RuntimeBusyError("Pi runtime is busy with another session")
            session_id = f"{kind}_{uuid.uuid4().hex[:12]}"
            session = {
                "id": session_id,
                "kind": kind,
                "created_at": time.monotonic(),
                "started_at_ms": self._now_ms(),
                "updated_at": self._now_ms(),
                "cancel_event": threading.Event(),
                **values,
            }
            self._sessions[session_id] = session
            self._active_session_id = session_id
            if kind == "scan":
                self._log("scan_started", "ok", json.dumps({
                    "session_id": session_id,
                    "purpose": session.get("purpose"),
                    "runtime": type(self).__name__,
                    "source": session.get("source", self.camera_source),
                    "started_at_ms": session["started_at_ms"],
                }))
            return session

    def _spawn(self, session_id: str, target: Any) -> None:
        thread = threading.Thread(target=target, args=(session_id,), daemon=True, name=f"pi-{session_id}")
        with self._lock:
            session = self._sessions.get(session_id)
            if session:
                session["thread"] = thread
        thread.start()

    def _get_view(self, session_id: str, kind: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session.get("kind") != kind:
                raise RuntimeRequestError(f"unknown {kind} session", 404)
            return self._scan_view(session) if kind == "scan" else self._enroll_view(session)

    def _cancel(self, session_id: str, kind: str) -> dict[str, Any]:
        with self._actuation.synchronized():
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.get("kind") != kind:
                    raise RuntimeRequestError(f"unknown {kind} session", 404)
                if session["state"] not in self._final_states(kind):
                    session["cancel_event"].set()
                    session.update(state="cancelled", message=f"{kind.capitalize()} cancelled.", updated_at=self._now_ms())
                    self._stop_camera_capture_now_locked(session_id)
                return self._scan_view(session) if kind == "scan" else self._enroll_view(session)

    def _session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._sessions.get(session_id)

    def _update(self, session_id: str, **updates: Any) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session["state"] not in {"cancelled", "error", "denied", "granted", "timeout", "completed"}:
                session.update(updates, updated_at=self._now_ms())

    def _finish(self, session_id: str, state: str, **updates: Any) -> None:
        log_detail = updates.get("message", "")
        log_failure = False
        timing = None
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session["state"] == "cancelled":
                return
            if session["kind"] == "scan" and not session.get("timing_recorded"):
                # Includes camera startup, matching and command dispatch, but
                # not phone/network latency or physical actuator completion.
                elapsed_ms = round(
                    (time.monotonic() - session["created_at"]) * 1000, 3
                )
                timing = {
                    "session_id": session_id,
                    "purpose": session.get("purpose"),
                    "runtime": type(self).__name__,
                    "source": session.get("source", self.camera_source),
                    "state": state,
                    "started_at_ms": session["started_at_ms"],
                    "finished_at_ms": self._now_ms(),
                    "duration_ms": elapsed_ms,
                    "message": log_detail,
                }
                session["timing_recorded"] = True
            session.update(state=state, updated_at=self._now_ms(), **updates)
            self._stop_camera_capture_now_locked(session_id)
            if state == "error":
                session["ok"] = False
                log_failure = session.get("kind") == "scan"
            elif state in {"granted", "denied", "timeout", "completed"}:
                session["ok"] = True
                log_failure = session.get("kind") == "scan" and state in {"denied", "timeout"}
        if log_failure:
            self._log("face_scan", "fail", log_detail or state)
        if timing is not None:
            self._log(
                "scan_timing",
                "ok" if state == "granted" else "fail",
                json.dumps(timing),
            )
        self._schedule_session_cleanup(session_id)

    def _release_session(self, session_id: str) -> None:
        with self._lock:
            if self._active_session_id == session_id:
                self._active_session_id = None
            self._stop_camera_capture_now_locked(session_id)
            session = self._sessions.get(session_id)
            timer = session.get("timeout_timer") if session else None
            if timer is not None and timer is not threading.current_thread():
                timer.cancel()
            if session and session["state"] not in self._final_states(session["kind"]):
                session.update(state="cancelled", message="Session stopped during cleanup.", updated_at=self._now_ms())
        self._schedule_session_cleanup(session_id)

    def _schedule_session_cleanup(self, session_id: str) -> None:
        timer = None

        def cleanup() -> None:
            try:
                self._remove_session(session_id)
            finally:
                with self._lock:
                    if timer is not None:
                        self._cleanup_timers.discard(timer)

        timer = threading.Timer(SESSION_TTL_SECONDS, cleanup)
        timer.daemon = True
        with self._lock:
            if self._paused:
                self._remove_session(session_id)
                return
            self._cleanup_timers.add(timer)
        timer.start()

    @staticmethod
    def _join_maintenance_threads(
        threads: list[Any],
        deadline: float,
    ) -> None:
        current = threading.current_thread()
        for thread in threads:
            if thread is None or thread is current:
                continue
            try:
                thread.join(max(0.0, deadline - time.monotonic()))
            except RuntimeError:
                if not thread.is_alive():
                    continue
            if thread.is_alive():
                raise RuntimeError(
                    f"{thread.name or 'runtime worker'} did not stop before timeout"
                )

    def _remove_session(self, session_id: str) -> None:
        with self._lock:
            if session_id != self._active_session_id:
                self._sessions.pop(session_id, None)

    def _cancel_all_sessions(self) -> None:
        client_session_ids = []
        with self._lock:
            for session in self._sessions.values():
                if session["state"] not in self._final_states(session["kind"]):
                    session["cancel_event"].set()
                    session.update(state="cancelled", message="Session cancelled by lock/reset.", updated_at=self._now_ms())
                    if session.get("source") == "client_camera":
                        client_session_ids.append(session["id"])
            self._stop_camera_capture_now_locked()
        for session_id in client_session_ids:
            self._release_session(session_id)

    def _invalidate_authorization(self, user_name: str | None = None) -> None:
        if not self._actuation.invalidate_authorization(user_name):
            return
        release_session_id = None
        with self._lock:
            self._stop_camera_capture_now_locked()
            session = self._sessions.get(self._active_session_id or "")
            if (
                session
                and session.get("kind") == "scan"
                and session.get("purpose") == "ignition"
                and session["state"] not in self._final_states("scan")
            ):
                session["cancel_event"].set()
                session.update(
                    state="cancelled",
                    message="Ignition authorization was revoked.",
                    updated_at=self._now_ms(),
                )
                if session.get("source") == "client_camera":
                    release_session_id = session["id"]
        if release_session_id:
            self._release_session(release_session_id)

    def _prune_sessions(self) -> None:
        now = time.monotonic()
        for sid, session in list(self._sessions.items()):
            if sid != self._active_session_id and session["state"] in self._final_states(session["kind"]):
                if now - session["created_at"] > SESSION_TTL_SECONDS:
                    self._sessions.pop(sid, None)
        if len(self._sessions) > 32:
            candidates = sorted(
                (s for s in self._sessions.values() if s["id"] != self._active_session_id),
                key=lambda s: s["created_at"],
            )
            for session in candidates[: len(self._sessions) - 32]:
                self._sessions.pop(session["id"], None)

    @staticmethod
    def _final_states(kind: str) -> set[str]:
        return {"granted", "denied", "error", "cancelled", "timeout", "completed"}

    def _scan_view(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": session.get("ok", session.get("state") != "error"),
            "session_id": session["id"],
            "purpose": session["purpose"],
            "source": session.get("source", self.camera_source),
            "state": session["state"],
            "user": session.get("user"),
            "score": session.get("score"),
            "face_count": session.get("face_count", 0),
            "message": session.get("message", ""),
            "matches": session.get("matches", 0),
            "window": session.get("window", {"matches": 0, "needed": MIN_MATCHES, "size": WINDOW_SIZE}),
            "command_sent": bool(session.get("command_sent", False)),
            "unlock_command_sent": bool(
                session.get("unlock_command_sent", False)
            ),
            "compensating_lock_command_sent": bool(
                session.get("compensating_lock_command_sent", False)
            ),
            "auto_relock_armed": bool(
                session.get("auto_relock_armed", False)
            ),
            "physical_state_confirmed": bool(
                session.get("physical_state_confirmed", False)
            ),
            "updated_at": session.get("updated_at", self._now_ms()),
        }

    def _enroll_view(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": session.get("ok", session.get("state") != "error"),
            "session_id": session["id"],
            "state": session["state"],
            "source": session.get("source", self.camera_source),
            "user": session["name"],
            "count": session.get("count", 0),
            "face_count": session.get("face_count", 0),
            "samples_needed": SAMPLES_NEEDED,
            "recognition_available": bool(session.get("recognition_available", False)),
            "message": session.get("message", ""),
            "updated_at": session.get("updated_at", self._now_ms()),
        }

    # ── Recognition and timers ───────────────────────────────────────────────

    def _load_authorized_database(self, face_engine: Any) -> dict[str, Any]:
        database = face_engine.load_database()
        allowed = {
            row["name"]
            for row in self._db.get_all_users()
            if row.get("face_access", 1)
        }
        return {name: embeddings for name, embeddings in database.items() if name in allowed}

    def _capture_bgr(self, camera: Any) -> Any:
        cv2 = self._load_modules()["cv2"]
        with self._camera_io_lock:
            frame = camera.capture_array()
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    def _start_camera_capture(
        self,
        session_id: str,
        camera: Any,
    ) -> SessionCameraCapture:
        with self._lock:
            session = self._sessions.get(session_id)
            if (
                not session
                or self._active_session_id != session_id
                or session.get("source") != self.camera_source
                or session["cancel_event"].is_set()
            ):
                raise RuntimeError("camera session is no longer active")
            if self._camera_capture is not None:
                raise RuntimeError("another camera capture session is still active")
            capture = SessionCameraCapture(
                session_id,
                camera,
                self._capture_bgr,
                self._encode_preview_jpeg,
                initial_frame_id=self._camera_frame_id,
            )
            self._camera_capture = capture
            try:
                capture.start()
            except Exception:
                if self._camera_capture is capture:
                    self._camera_capture = None
                raise
        return capture

    def _wait_for_camera_frame(
        self,
        capture: SessionCameraCapture,
        frame_id: int,
        deadline: float,
        cancel_event: threading.Event,
    ) -> CapturedFrame | None:
        if cancel_event.is_set():
            return None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        frame = capture.wait_for_newer(frame_id, min(0.5, remaining))
        if frame:
            return (
                frame
                if self._camera_capture_can_continue(
                    capture,
                    deadline,
                    cancel_event,
                )
                else None
            )
        if cancel_event.is_set():
            return None
        failure = capture.failure()
        if failure:
            raise RuntimeError(f"{self.camera_label} {failure}")
        if not self._camera_capture_is_active(capture.session_id, capture):
            raise RuntimeError(f"{self.camera_label} capture session ended")
        return None

    def _camera_capture_can_continue(
        self,
        capture: SessionCameraCapture,
        deadline: float,
        cancel_event: threading.Event,
    ) -> bool:
        if cancel_event.is_set() or time.monotonic() >= deadline:
            return False
        failure = capture.failure()
        if failure:
            raise RuntimeError(f"{self.camera_label} {failure}")
        if not self._camera_capture_is_active(capture.session_id, capture):
            raise RuntimeError(f"{self.camera_label} capture session ended")
        return True

    def _encode_preview_jpeg(self, frame: Any) -> bytes | None:
        cv2 = self._load_modules()["cv2"]
        height, width = frame.shape[:2]
        preview = frame
        if width > PREVIEW_MAX_WIDTH:
            preview_height = max(1, round(height * PREVIEW_MAX_WIDTH / width))
            preview = cv2.resize(
                frame,
                (PREVIEW_MAX_WIDTH, preview_height),
                interpolation=cv2.INTER_AREA,
            )
        encoded, jpeg = cv2.imencode(
            ".jpg",
            preview,
            [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_JPEG_QUALITY],
        )
        return jpeg.tobytes() if encoded else None

    def _active_camera_capture(
        self,
        session_id: str,
    ) -> SessionCameraCapture | None:
        with self._lock:
            capture = self._camera_capture
            session = self._sessions.get(session_id)
            if (
                not capture
                or capture.session_id != session_id
                or not capture.active
                or self._active_session_id != session_id
                or not session
                or session.get("source") != self.camera_source
                or session["cancel_event"].is_set()
                or session["state"] in self._final_states(session["kind"])
            ):
                return None
            return capture

    def _camera_capture_is_active(
        self,
        session_id: str,
        capture: SessionCameraCapture,
    ) -> bool:
        return self._active_camera_capture(session_id) is capture

    def _stop_camera_capture_now_locked(
        self,
        session_id: str | None = None,
    ) -> None:
        capture = self._camera_capture
        if capture and (session_id is None or capture.session_id == session_id):
            capture.request_stop()

    @staticmethod
    def _multipart_frame(frame: CapturedFrame) -> bytes:
        jpeg = frame.jpeg or b""
        return (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n"
            + f"Content-Length: {len(jpeg)}\r\n".encode("ascii")
            + f"X-Frame-Id: {frame.frame_id}\r\n\r\n".encode("ascii")
            + jpeg
            + b"\r\n"
        )

    @staticmethod
    def _window_candidate(history: deque[tuple[bool, str | None]]) -> tuple[str | None, int]:
        counts = Counter(user for matched, user in history if matched and user)
        if not counts:
            return None, 0
        candidate, count = counts.most_common(1)[0]
        return candidate, count

    @staticmethod
    def _scan_message(face_count: int, result: dict[str, Any], candidate: str | None, count: int) -> str:
        if face_count == 0:
            return f"No face detected ({count}/{WINDOW_SIZE})."
        if face_count > 1:
            return f"Multiple faces detected ({count}/{WINDOW_SIZE})."
        if result.get("matched"):
            return f"Matched {candidate or result.get('user')} ({count}/{WINDOW_SIZE})."
        return f"Face not recognized ({count}/{WINDOW_SIZE})."

    def _safety_settings(self) -> RuntimeSafetySettings:
        try:
            validated = validate_runtime_safety_settings(
                self._db.get_settings_for_ui(),
                require_complete=self._authorization.required,
                liveness_available=self.liveness_available,
            )
        except Exception as exc:
            message = f"Safety settings unavailable: {exc}"
            with self._lock:
                self._settings_error = message
            raise RuntimeError(message) from exc

        with self._lock:
            self._settings_error = None
        return validated

    def _refresh_safety_settings(self) -> bool:
        try:
            self._safety_settings()
            return True
        except RuntimeError:
            return False

    def _require_safety_settings(self) -> RuntimeSafetySettings:
        try:
            return self._safety_settings()
        except RuntimeError as exc:
            raise RuntimeRequestError(str(exc), 503) from exc

    def _close_camera(self, owner_id: str | None = None) -> bool:
        with self._lock:
            if owner_id is not None and self._camera_owner_id not in (None, owner_id):
                return True
            capture = self._camera_capture
        if capture is not None:
            if not capture.stop(self._camera_close_timeout()):
                self._record_error(
                    "camera capture did not stop before teardown timeout",
                    "camera",
                )
                return False
            with self._lock:
                if self._camera_capture is capture:
                    self._camera_frame_id = max(
                        self._camera_frame_id,
                        capture.frame_id,
                    )
                    self._camera_capture = None

        if not self._camera_io_lock.acquire(timeout=self._camera_close_timeout()):
            self._record_error("camera capture did not stop before teardown timeout", "camera")
            return False
        try:
            with self._lock:
                if owner_id is not None and self._camera_owner_id not in (None, owner_id):
                    return True
                camera = self._camera
            if camera is not None:
                cleanup_errors = []
                for method in ("stop", "close", "release"):
                    try:
                        callback = getattr(camera, method, None)
                        if callback:
                            callback()
                    except Exception as exc:
                        cleanup_errors.append(f"{method}: {exc}")
                if cleanup_errors:
                    self._record_error(f"camera teardown failed ({'; '.join(cleanup_errors)})", "camera")
                    return False
            with self._lock:
                if self._camera is camera:
                    self._camera = None
                    self._camera_owner_id = None
            return True
        finally:
            self._camera_io_lock.release()

    def _defer_camera_cleanup(self, session_id: str) -> None:
        with self._lock:
            existing = self._camera_cleanup_thread
            if existing and existing.is_alive():
                return

            def cleanup_when_capture_stops() -> None:
                try:
                    with self._lock:
                        capture = self._camera_capture
                    if capture and capture.session_id == session_id:
                        capture.join()
                    if self._close_camera(session_id):
                        self._release_session(session_id)
                finally:
                    with self._lock:
                        if self._camera_cleanup_thread is threading.current_thread():
                            self._camera_cleanup_thread = None

            cleanup_thread = threading.Thread(
                target=cleanup_when_capture_stops,
                daemon=True,
                name=f"camera-cleanup-{session_id}",
            )
            self._camera_cleanup_thread = cleanup_thread
        cleanup_thread.start()

    def _record_error(self, message: str, category: str) -> None:
        with self._lock:
            if category == "hardware":
                self._hardware_error = message
            elif category == "model":
                self._model_error = message
            elif category == "camera":
                self._camera_error = message
            elif category == "serial":
                self._serial_error = message

    def _log(self, stage: str, result: str, detail: str, user_id: str | None = None) -> None:
        try:
            self._db.log_event(stage, result, detail=detail, user_id=user_id)
        except Exception:
            pass

    @staticmethod
    def _bounded_seconds(env_name: str, default: int) -> int:
        try:
            value = int(os.environ.get(env_name, default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, 900))

    @staticmethod
    def _enroll_sample_interval() -> float:
        try:
            value = float(os.environ.get("PI_ENROLL_SAMPLE_INTERVAL_SECONDS", DEFAULT_ENROLL_SAMPLE_INTERVAL))
        except (TypeError, ValueError):
            value = DEFAULT_ENROLL_SAMPLE_INTERVAL
        return max(0.0, min(value, 5.0))

    @staticmethod
    def _camera_close_timeout() -> float:
        try:
            value = float(os.environ.get("PI_CAMERA_CLOSE_TIMEOUT_SECONDS", DEFAULT_CAMERA_CLOSE_TIMEOUT))
        except (TypeError, ValueError):
            value = DEFAULT_CAMERA_CLOSE_TIMEOUT
        return max(0.1, min(value, 10.0))

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)
