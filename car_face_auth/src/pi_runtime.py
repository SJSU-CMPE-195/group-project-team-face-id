"""Single-process Pi camera, face-recognition, and ESP32 runtime.

The module deliberately keeps Raspberry Pi-only imports lazy.  The Device API
can therefore be imported and its non-hardware routes tested on a workstation.
"""

from __future__ import annotations

from collections import Counter, deque
import importlib
import os
import threading
import time
import uuid
from typing import Any


WINDOW_SIZE = 10
MIN_MATCHES = 6
SAMPLES_NEEDED = 10
DEFAULT_SCAN_TIMEOUT = 20
DEFAULT_ENROLL_TIMEOUT = 180
DEFAULT_ENROLL_SAMPLE_INTERVAL = 0.5
DEFAULT_CAMERA_CLOSE_TIMEOUT = 2.0
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

    def __init__(self, db_api: Any, *, face_engine: Any | None = None):
        self._db = db_api
        self._face_engine = face_engine
        self._lock = threading.RLock()
        self._actuator_lock = threading.RLock()
        self._camera_io_lock = threading.Lock()
        self._serial_lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._active_session_id: str | None = None
        self._modules: dict[str, Any] | None = None
        self._model: Any = None
        self._camera: Any = None
        self._camera_owner_id: str | None = None
        self._serial: Any = None
        self._serial_port: str | None = None
        self._hardware_error: str | None = None
        self._model_error: str | None = None
        self._camera_error: str | None = None
        self._serial_error: str | None = None
        self._ignition_on = False
        self._unlock_owner: str | None = None
        self._authorization_generation = 0
        self._closed = False
        self._auto_relock_timer: threading.Timer | None = None
        self._ignition_stop_timer: threading.Timer | None = None

    # ── Public status and session API ────────────────────────────────────────

    def status(self) -> dict[str, Any]:
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
            actuator_ready = self._serial is not None
            return {
                "ready": bool(not self._closed and camera_ready and actuator_ready),
                "hardware": "closed" if self._closed else ("ready" if camera_ready and actuator_ready else (
                    "degraded" if camera_ready else (
                        "unavailable"
                        if self._hardware_error or self._model_error or self._camera_error or self._serial_error
                        else "not_initialized"
                    )
                )),
                "dependencies_loaded": modules_loaded,
                "model_loaded": self._model is not None,
                "camera_open": self._camera is not None,
                "esp32_connected": self._serial is not None,
                "serial_port": self._serial_port,
                "ignitionOn": self._ignition_on,
                "ignition_authorized": self._unlock_owner is not None,
                "active_session": active_view,
                "error": self._hardware_error or self._model_error or self._camera_error or self._serial_error,
            }

    def start_scan(self, purpose: str = "unlock", expected_user: str | None = None) -> dict[str, Any]:
        purpose = (purpose or "unlock").strip().lower()
        if purpose not in ("unlock", "ignition"):
            raise RuntimeRequestError("purpose must be unlock or ignition", 400)
        expected_user = (expected_user or "").strip() or None
        if purpose == "ignition":
            with self._lock:
                unlock_owner = self._unlock_owner
                authorization_generation = self._authorization_generation
            if not unlock_owner:
                raise RuntimeRequestError("a face-verified unlock is required before ignition", 409)
            if expected_user and expected_user.casefold() != unlock_owner.casefold():
                raise RuntimeRequestError("expected_user does not match the active unlock", 409)
            expected_user = unlock_owner
            try:
                if self._db.get_status().get("lockState") == "locked":
                    raise RuntimeRequestError("device is locked", 409)
            except RuntimeRequestError:
                raise
            except Exception as exc:
                raise RuntimeRequestError(f"could not read device state: {exc}", 503) from exc

        session = self._new_session(
            "scan",
            {
                "purpose": purpose,
                "expected_user": expected_user,
                "authorization_generation": authorization_generation if purpose == "ignition" else None,
                "state": "starting",
                "user": None,
                "score": None,
                "face_count": 0,
                "matches": 0,
                "message": "Pi camera scan starting.",
                "window": {"matches": 0, "needed": MIN_MATCHES, "size": WINDOW_SIZE},
            },
        )
        self._spawn(session["id"], self._run_scan)
        return self._scan_view(session)

    def scan_status(self, session_id: str) -> dict[str, Any]:
        return self._get_view(session_id, "scan")

    def cancel_scan(self, session_id: str) -> dict[str, Any]:
        return self._cancel(session_id, "scan")

    def start_enrollment(self, name: str) -> dict[str, Any]:
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
        name = users[0]["name"]
        session = self._new_session(
            "enroll",
            {
                "name": name,
                "state": "capturing",
                "count": 0,
                "message": "Pi camera enrollment starting.",
            },
        )
        self._spawn(session["id"], self._run_enrollment)
        return self._enroll_view(session)

    def enrollment_status(self, session_id: str) -> dict[str, Any]:
        return self._get_view(session_id, "enroll")

    def cancel_enrollment(self, session_id: str) -> dict[str, Any]:
        return self._cancel(session_id, "enroll")

    # ── Actuator lifecycle ───────────────────────────────────────────────────

    def unlock(self, reason: str = "manual_ui") -> dict[str, Any]:
        with self._actuator_lock:
            with self._lock:
                if self._closed:
                    return {"ok": False, "error": "Pi runtime is shutting down"}
                self._unlock_owner = None
                self._authorization_generation += 1
            if not self._send_command("UNLOCK"):
                return {"ok": False, "error": self._serial_error or "ESP32 is unavailable"}
            try:
                self._db.set_unlock(reason=reason)
            except Exception as exc:
                self._send_command("LOCK", connect=False)
                return {"ok": False, "error": f"unlock state could not be saved: {exc}"}
            self._schedule_auto_relock()
            return {"ok": True}

    def set_ignition(self, running: bool, reason: str = "manual_ui") -> dict[str, Any]:
        running = bool(running)
        with self._actuator_lock:
            with self._lock:
                if self._closed:
                    return {"ok": False, "error": "Pi runtime is shutting down"}
                already = self._ignition_on == running
            if running:
                try:
                    if self._db.get_status().get("lockState") == "locked":
                        return {"ok": False, "error": "device is locked"}
                except Exception as exc:
                    return {"ok": False, "error": f"could not read device state: {exc}"}
                if already:
                    return {"ok": True, "ignitionOn": True}
            if not self._send_command("START" if running else "STOP"):
                return {"ok": False, "error": self._serial_error or "ESP32 is unavailable"}
            with self._lock:
                self._ignition_on = running
            self._cancel_ignition_stop_timer()
            if running:
                self._schedule_ignition_stop()
            self._log("ignition", "ok", f"{'start' if running else 'stop'}:{reason}")
            return {"ok": True, "ignitionOn": running}

    def force_lock(self, reason: str = "manual_ui") -> dict[str, Any]:
        """Cancel work and send both safe actuator commands before locking DB state."""
        self._cancel_all_sessions()
        self._cancel_auto_relock_timer()
        self._cancel_ignition_stop_timer()
        with self._actuator_lock:
            stop_ok = self._send_command("STOP")
            lock_ok = self._send_command("LOCK")
            with self._lock:
                self._ignition_on = False
                self._unlock_owner = None
                self._authorization_generation += 1
            try:
                self._db.set_lock(reason=reason)
            except Exception as exc:
                self._close_camera()
                return {"ok": False, "error": f"lock state could not be saved: {exc}"}
        # A stuck camera teardown must not delay the physical STOP/LOCK above.
        camera_closed = self._close_camera()
        if not (stop_ok and lock_ok):
            return {"ok": False, "error": self._serial_error or "ESP32 is unavailable", "locked": True}
        if not camera_closed:
            return {
                "ok": False,
                "error": self._camera_error or "camera teardown did not complete",
                "locked": True,
            }
        return {"ok": True, "locked": True}

    def close(self) -> None:
        """Best-effort process shutdown: stop, lock, then release hardware."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            had_hardware = self._serial is not None or self._modules is not None
        self._cancel_all_sessions()
        self._cancel_auto_relock_timer()
        self._cancel_ignition_stop_timer()
        with self._actuator_lock:
            if had_hardware:
                self._send_command("STOP")
                self._send_command("LOCK")
            with self._lock:
                self._ignition_on = False
                self._unlock_owner = None
                self._authorization_generation += 1
            try:
                self._db.set_lock(reason="shutdown")
            except Exception:
                pass
        self._close_camera()
        self._close_serial()

    def delete_user(self, user_id: str) -> dict[str, Any]:
        """Serialize authorization mutations with the final grant decision."""
        with self._actuator_lock:
            user = self._db.get_user_by_id(user_id)
            result = self._db.delete_user(user_id)
            if result.get("ok") and user:
                self._invalidate_authorization(user.get("name"))
            return result

    def set_user_access(self, user_id: str, allowed: bool) -> dict[str, Any]:
        """Apply access changes atomically with respect to scan actuation."""
        with self._actuator_lock:
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
            message = f"Pi camera unavailable: {exc}"
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
        try:
            connection = self._ensure_serial() if connect else self._serial
            if connection is None:
                raise RuntimeError("ESP32 serial device is not connected")
            with self._serial_lock:
                connection.write((command + "\n").encode("ascii"))
                flush = getattr(connection, "flush", None)
                if flush:
                    flush()
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
            self._update(session_id, state="scanning", message="Pi camera is scanning.")
            face_engine = self._get_face_engine()
            if cancel_event.is_set():
                return
            model = self._ensure_model()
            if cancel_event.is_set():
                return
            camera = self._open_camera(session_id)
            if cancel_event.is_set():
                return
            database = self._load_authorized_database(face_engine)
            if not database:
                raise RuntimeError("No enrolled face with access enabled")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    return
                frame = self._capture_bgr(camera)
                result = face_engine.analyze_frame(model, frame, database)
                matched = bool(result.get("matched") and result.get("user"))
                user = result.get("user") if matched else None
                history.append((matched, user))
                candidate, count = self._window_candidate(history)
                face_count = int(result.get("face_count") or 0)
                self._update(
                    session_id,
                    user=result.get("user"),
                    score=result.get("score"),
                    face_count=face_count,
                    matches=count,
                    window={"matches": count, "needed": MIN_MATCHES, "size": WINDOW_SIZE},
                    message=self._scan_message(face_count, result, candidate, count),
                )
                if count >= MIN_MATCHES:
                    self._grant_scan(session_id, candidate, result.get("score"), cancel_event)
                    return
            if not cancel_event.is_set():
                self._finish(session_id, "timeout", message=f"Scan timed out after {timeout} seconds.")
        except Exception as exc:
            if not cancel_event.is_set():
                self._finish(session_id, "error", message=str(exc))
        finally:
            self._close_camera(session_id)
            self._release_session(session_id)

    def _run_enrollment(self, session_id: str) -> None:
        session = self._session(session_id)
        if not session:
            return
        cancel_event = session["cancel_event"]
        embeddings: list[Any] = []
        timeout = self._bounded_seconds("PI_ENROLL_TIMEOUT_SECONDS", DEFAULT_ENROLL_TIMEOUT)
        try:
            self._update(session_id, state="capturing", message="Pi camera enrollment is capturing samples.")
            face_engine = self._get_face_engine()
            if cancel_event.is_set():
                return
            model = self._ensure_model()
            if cancel_event.is_set():
                return
            camera = self._open_camera(session_id)
            if cancel_event.is_set():
                return
            deadline = time.monotonic() + timeout
            while len(embeddings) < SAMPLES_NEEDED and time.monotonic() < deadline:
                if cancel_event.is_set():
                    return
                frame = self._capture_bgr(camera)
                embedding, face_count = face_engine.extract_single_face_embedding(model, frame)
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
            with self._actuator_lock:
                if cancel_event.is_set():
                    return
                name = session["name"]
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
            self._close_camera(session_id)
            self._release_session(session_id)

    def _grant_scan(self, session_id: str, candidate: str | None, score: Any, cancel_event: threading.Event) -> None:
        session = self._session(session_id)
        if not session or cancel_event.is_set():
            return
        if not candidate:
            self._finish(session_id, "denied", score=score, message="No authorized user matched the rolling window.")
            return
        with self._actuator_lock:
            session = self._session(session_id)
            with self._lock:
                shutting_down = self._closed
            if not session or cancel_event.is_set() or shutting_down:
                return
            try:
                authorized_rows = [
                    row for row in self._db.get_all_users()
                    if row.get("name") == candidate and row.get("face_access", 1)
                ]
                if len(authorized_rows) != 1:
                    self._finish(session_id, "denied", user=candidate, score=score, message="Access was revoked before the scan completed.")
                    return
            except Exception as exc:
                self._finish(session_id, "error", message=f"could not confirm face access: {exc}")
                return
            purpose = session["purpose"]
            if purpose == "ignition":
                expected_user = session.get("expected_user")
                with self._lock:
                    current_owner = self._unlock_owner
                    current_generation = self._authorization_generation
                authorization_is_current = (
                    current_owner is not None
                    and expected_user is not None
                    and candidate.casefold() == expected_user.casefold() == current_owner.casefold()
                    and session.get("authorization_generation") == current_generation
                )
                if not authorization_is_current:
                    self._finish(session_id, "denied", user=candidate, score=score, message="Ignition denied: face did not match the unlocked driver.")
                    return
                try:
                    if self._db.get_status().get("lockState") == "locked":
                        self._finish(session_id, "denied", user=candidate, score=score, message="Ignition denied because the device is locked.")
                        return
                except Exception as exc:
                    self._finish(session_id, "error", message=f"could not read device state: {exc}")
                    return
                if cancel_event.is_set():
                    return
                result = self.set_ignition(True, reason=f"scan:{session_id}")
                if not result.get("ok"):
                    self._finish(session_id, "error", user=candidate, score=score, message=result.get("error", "Could not start ignition."))
                    return
                self._finish(session_id, "granted", user=candidate, score=score, matches=MIN_MATCHES, message="Ignition granted for the same driver.")
                return
            if cancel_event.is_set():
                return
            if not self._send_command("UNLOCK"):
                self._finish(session_id, "error", user=candidate, score=score, message=self._serial_error or "ESP32 is unavailable; unlock was not applied.")
                return
            try:
                self._db.set_unlock(reason=f"scan:{session_id}")
                with self._lock:
                    self._unlock_owner = candidate
                    self._authorization_generation += 1
                self._log("face_scan", "ok", f"Granted: {candidate}", authorized_rows[0].get("id"))
            except Exception as exc:
                self._send_command("LOCK", connect=False)
                with self._lock:
                    self._unlock_owner = None
                self._finish(session_id, "error", user=candidate, score=score, message=f"unlock state could not be saved: {exc}")
                return
            self._schedule_auto_relock()
            self._finish(session_id, "granted", user=candidate, score=score, matches=MIN_MATCHES, message="Pi camera unlock granted.")

    # ── Session bookkeeping ──────────────────────────────────────────────────

    def _new_session(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeRequestError("Pi runtime is shutting down", 503)
            self._prune_sessions()
            if self._active_session_id and self._active_session_id in self._sessions:
                raise RuntimeBusyError("Pi camera is busy with another session")
            session_id = f"{kind}_{uuid.uuid4().hex[:12]}"
            session = {
                "id": session_id,
                "kind": kind,
                "created_at": time.monotonic(),
                "updated_at": self._now_ms(),
                "cancel_event": threading.Event(),
                **values,
            }
            self._sessions[session_id] = session
            self._active_session_id = session_id
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
        with self._actuator_lock:
            with self._lock:
                session = self._sessions.get(session_id)
                if not session or session.get("kind") != kind:
                    raise RuntimeRequestError(f"unknown {kind} session", 404)
                if session["state"] not in self._final_states(kind):
                    session["cancel_event"].set()
                    session.update(state="cancelled", message=f"{kind.capitalize()} cancelled.", updated_at=self._now_ms())
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
        with self._lock:
            session = self._sessions.get(session_id)
            if not session or session["state"] == "cancelled":
                return
            session.update(state=state, updated_at=self._now_ms(), **updates)
            if state == "error":
                session["ok"] = False
                log_failure = session.get("kind") == "scan"
            elif state in {"granted", "denied", "timeout", "completed"}:
                session["ok"] = True
                log_failure = session.get("kind") == "scan" and state in {"denied", "timeout"}
        if log_failure:
            self._log("face_scan", "fail", log_detail or state)
        self._schedule_session_cleanup(session_id)

    def _release_session(self, session_id: str) -> None:
        with self._lock:
            if self._active_session_id == session_id:
                self._active_session_id = None
            session = self._sessions.get(session_id)
            if session and session["state"] not in self._final_states(session["kind"]):
                session.update(state="cancelled", message="Session stopped during cleanup.", updated_at=self._now_ms())
        self._schedule_session_cleanup(session_id)

    def _schedule_session_cleanup(self, session_id: str) -> None:
        timer = threading.Timer(SESSION_TTL_SECONDS, self._remove_session, args=(session_id,))
        timer.daemon = True
        timer.start()

    def _remove_session(self, session_id: str) -> None:
        with self._lock:
            if session_id != self._active_session_id:
                self._sessions.pop(session_id, None)

    def _cancel_all_sessions(self) -> None:
        with self._lock:
            for session in self._sessions.values():
                if session["state"] not in self._final_states(session["kind"]):
                    session["cancel_event"].set()
                    session.update(state="cancelled", message="Session cancelled by lock/reset.", updated_at=self._now_ms())

    def _invalidate_authorization(self, user_name: str | None = None) -> None:
        with self._lock:
            if user_name and self._unlock_owner and user_name.casefold() != self._unlock_owner.casefold():
                return
            self._unlock_owner = None
            self._authorization_generation += 1
            session = self._sessions.get(self._active_session_id or "")
            if session and session.get("kind") == "scan" and session.get("purpose") == "ignition":
                if session["state"] not in self._final_states("scan"):
                    session["cancel_event"].set()
                    session.update(
                        state="cancelled",
                        message="Ignition authorization was revoked.",
                        updated_at=self._now_ms(),
                    )

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
            "state": session["state"],
            "user": session.get("user"),
            "score": session.get("score"),
            "face_count": session.get("face_count", 0),
            "message": session.get("message", ""),
            "matches": session.get("matches", 0),
            "window": session.get("window", {"matches": 0, "needed": MIN_MATCHES, "size": WINDOW_SIZE}),
            "updated_at": session.get("updated_at", self._now_ms()),
        }

    def _enroll_view(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": session.get("ok", session.get("state") != "error"),
            "session_id": session["id"],
            "state": session["state"],
            "source": "pi_camera",
            "user": session["name"],
            "count": session.get("count", 0),
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

    def _schedule_auto_relock(self) -> None:
        try:
            seconds = int(self._db.get_settings_for_ui().get("autoRelockSeconds", 0) or 0)
        except Exception:
            seconds = 0
        self._cancel_auto_relock_timer()
        if seconds <= 0:
            return
        timer = None

        def relock_if_current():
            with self._actuator_lock:
                with self._lock:
                    if self._closed or self._auto_relock_timer is not timer:
                        return
                    self._auto_relock_timer = None
                self.force_lock(reason=f"auto_relock_{seconds}s")

        timer = threading.Timer(seconds, relock_if_current)
        timer.daemon = True
        with self._lock:
            self._auto_relock_timer = timer
        timer.start()

    def _schedule_ignition_stop(self) -> None:
        try:
            seconds = int(self._db.get_settings_for_ui().get("ignitionAutoStopSeconds", 0) or 0)
        except Exception:
            seconds = 0
        if seconds <= 0:
            return
        timer = None

        def stop_if_current():
            with self._actuator_lock:
                with self._lock:
                    if self._closed or self._ignition_stop_timer is not timer:
                        return
                    self._ignition_stop_timer = None
                self.set_ignition(False, reason=f"ignition_timeout_{seconds}s")

        timer = threading.Timer(seconds, stop_if_current)
        timer.daemon = True
        with self._lock:
            self._ignition_stop_timer = timer
        timer.start()

    def _cancel_auto_relock_timer(self) -> None:
        with self._lock:
            timer, self._auto_relock_timer = self._auto_relock_timer, None
        if timer:
            timer.cancel()

    def _cancel_ignition_stop_timer(self) -> None:
        with self._lock:
            timer, self._ignition_stop_timer = self._ignition_stop_timer, None
        if timer:
            timer.cancel()

    def _close_camera(self, owner_id: str | None = None) -> bool:
        if not self._camera_io_lock.acquire(timeout=self._camera_close_timeout()):
            self._record_error("camera capture did not stop before teardown timeout", "camera")
            return False
        try:
            with self._lock:
                if owner_id is not None and self._camera_owner_id not in (None, owner_id):
                    return True
                camera, self._camera = self._camera, None
                self._camera_owner_id = None
            if camera is not None:
                cleanup_errors = []
                for method in ("stop", "close"):
                    try:
                        callback = getattr(camera, method, None)
                        if callback:
                            callback()
                    except Exception as exc:
                        cleanup_errors.append(f"{method}: {exc}")
                if cleanup_errors:
                    self._record_error(f"camera teardown failed ({'; '.join(cleanup_errors)})", "camera")
                    return False
            return True
        finally:
            self._camera_io_lock.release()

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
