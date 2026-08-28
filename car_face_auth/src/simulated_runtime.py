"""Dependency-free Pi runtime for local UI and failure-mode simulation.

``SimulatedPiRuntime`` deliberately subclasses :class:`PiRuntime` instead of
copying its workers.  The production rolling-window, enrollment, authorization
and actuator paths therefore remain the code under test; this module only
replaces the camera, model, face engine and serial seams.
"""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from typing import Any

from .pi_runtime import PiRuntime


_COMMANDS = frozenset(("LOCK", "UNLOCK", "START", "STOP"))
_SCENARIO_KEYS = frozenset(
    ("frames", "frame_delay_ms", "camera_error", "camera_stalled")
)
_CONFIG_KEYS = _SCENARIO_KEYS | frozenset(("scenario", "serial_connected", "fail_commands"))


class SimulationConfigError(ValueError):
    """Raised when a simulation configuration is not a strict JSON object."""


class _SimulatedSerial:
    """Small serial-port stand-in used by the inherited actuator code."""

    is_open = True

    def write(self, _payload: bytes) -> int:
        if not self.is_open:
            raise OSError("simulated ESP32 is disconnected")
        return len(_payload)

    def flush(self) -> None:
        if not self.is_open:
            raise OSError("simulated ESP32 is disconnected")
        return None

    def close(self) -> None:
        self.is_open = False


class _SimulatedCamera:
    """Camera that can be delayed, stalled, or failed without hardware."""

    def __init__(self, runtime: "SimulatedPiRuntime", owner_id: str | None):
        self.runtime = runtime
        self.owner_id = owner_id
        self.frame_index = 0
        self.closed = False

    def capture_array(self) -> dict[str, Any] | None:
        runtime = self.runtime
        while True:
            with runtime._simulation_lock:
                stalled = runtime._scenario["camera_stalled"]
                closed = self.closed or runtime._closed
                camera_error = runtime._scenario["camera_error"]
            if closed:
                return None
            if camera_error:
                raise RuntimeError(camera_error)
            session = runtime._session(self.owner_id) if self.owner_id else None
            cancelled = bool(session and session["cancel_event"].is_set())
            if cancelled:
                return None
            if not stalled:
                break
            # Poll the session and configuration rather than waiting on an
            # uninterruptible Event.  Cancellation, reset, close, and a later
            # configure(camera_stalled=False) all release this loop promptly.
            time.sleep(0.01)

        with runtime._simulation_lock:
            delay_seconds = runtime._scenario["frame_delay_ms"] / 1000.0
        if delay_seconds:
            deadline = time.monotonic() + delay_seconds
            while time.monotonic() < deadline:
                session = runtime._session(self.owner_id) if self.owner_id else None
                if self.closed or runtime._closed or (session and session["cancel_event"].is_set()):
                    return None
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

        with runtime._simulation_lock:
            frames = runtime._scenario["frames"]
            frame = deepcopy(frames[min(self.frame_index, len(frames) - 1)])
            self.frame_index += 1
        return frame

    def stop(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


class _SimulatedFaceEngine:
    """Face-engine contract implemented with scenario metadata only."""

    def __init__(self, runtime: "SimulatedPiRuntime"):
        self.runtime = runtime
        self._saved: dict[str, list[Any]] = {}
        self._lock = threading.RLock()

    def load_database(self) -> dict[str, list[Any]]:
        # A configured identity is enough to exercise the recognition path.
        # Existing users are represented as simulated enrolled records so the
        # local UI can run before a real InsightFace database exists.
        with self._lock:
            database = deepcopy(self._saved)
        try:
            users = self.runtime._db.get_all_users()
        except Exception:
            users = []
        for user in users:
            name = (user.get("name") or "").strip()
            if name and user.get("face_access", user.get("faceAccess", 1)):
                database.setdefault(name, [{"identity": name}])
        return database

    def analyze_frame(self, _model: Any, frame: Any, database: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(frame, dict):
            return {"ok": False, "face_count": 0, "matched": False, "user": None, "score": None}
        face_count = frame["face_count"]
        identity = frame["identity"]
        score = frame["score"]
        if face_count != 1:
            return {
                "ok": True,
                "face_count": face_count,
                "matched": False,
                "user": None,
                "score": score,
                "bbox": None,
            }
        matched = bool(identity and identity in database and (score is None or score >= 0.45))
        return {
            "ok": True,
            "face_count": 1,
            "matched": matched,
            "user": identity if matched else None,
            "score": score,
            "bbox": [0.0, 0.0, 1.0, 1.0],
        }

    def extract_single_face_embedding(self, _model: Any, frame: Any) -> tuple[Any | None, int]:
        if not isinstance(frame, dict):
            return None, 0
        if frame["face_count"] != 1:
            return None, frame["face_count"]
        return {"identity": frame["identity"], "sample": time.monotonic_ns()}, 1

    def decode_image_bytes(self, data: bytes) -> dict[str, Any] | None:
        if not data:
            return None
        with self.runtime._simulation_lock:
            return deepcopy(self.runtime._scenario["frames"][0])

    def save_user_embedding(self, name: str, embeddings: list[Any]) -> dict[str, Any]:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "name is required"}
        if not isinstance(embeddings, list) or not embeddings:
            return {"ok": False, "error": "embeddings are required"}
        with self._lock:
            self._saved[name] = deepcopy(embeddings)
        return {"ok": True}


class SimulatedPiRuntime(PiRuntime):
    """A thread-safe, configurable PiRuntime substitute for workstation use."""

    def __init__(self, db_api: Any, scenario: dict[str, Any] | None = None, *, serial_connected: bool = True):
        super().__init__(db_api, face_engine=None)
        self._simulation_lock = threading.RLock()
        self._command_log: list[dict[str, Any]] = []
        self._serial_connected = self._validate_bool(serial_connected, "serial_connected")
        self._fail_commands: set[str] = set()
        self._scenario = self._default_scenario()
        self._simulated_face_engine = _SimulatedFaceEngine(self)
        if scenario is not None:
            self.configure(scenario)

    @staticmethod
    def _default_scenario() -> dict[str, Any]:
        return {
            "frames": [{"identity": None, "face_count": 0, "score": None}],
            "frame_delay_ms": 0,
            "camera_error": None,
            "camera_stalled": False,
        }

    @staticmethod
    def _validate_bool(value: Any, field: str) -> bool:
        if not isinstance(value, bool):
            raise SimulationConfigError(f"{field} must be a boolean")
        return value

    @staticmethod
    def _validate_scenario(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise SimulationConfigError("scenario must be an object")
        unknown = set(raw) - _SCENARIO_KEYS
        if unknown:
            raise SimulationConfigError(f"unknown scenario field(s): {', '.join(sorted(unknown))}")
        frames_raw = raw.get("frames", SimulatedPiRuntime._default_scenario()["frames"])
        if not isinstance(frames_raw, list) or not frames_raw:
            raise SimulationConfigError("frames must be a non-empty array")
        frames: list[dict[str, Any]] = []
        for index, frame in enumerate(frames_raw):
            if not isinstance(frame, dict):
                raise SimulationConfigError(f"frames[{index}] must be an object")
            unknown_frame = set(frame) - {"identity", "face_count", "score"}
            if unknown_frame:
                raise SimulationConfigError(
                    f"unknown frames[{index}] field(s): {', '.join(sorted(unknown_frame))}"
                )
            identity = frame.get("identity")
            if identity is not None and (not isinstance(identity, str) or not identity.strip()):
                raise SimulationConfigError(f"frames[{index}].identity must be a non-empty string or null")
            face_count = frame.get("face_count", 1 if identity else 0)
            if isinstance(face_count, bool) or not isinstance(face_count, int) or not 0 <= face_count <= 16:
                raise SimulationConfigError(f"frames[{index}].face_count must be an integer from 0 to 16")
            score = frame.get("score", 0.91 if identity and face_count == 1 else None)
            if score is not None and (
                isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1
            ):
                raise SimulationConfigError(f"frames[{index}].score must be a number from 0 to 1 or null")
            frames.append(
                {
                    "identity": identity.strip() if isinstance(identity, str) else None,
                    "face_count": face_count,
                    "score": float(score) if score is not None else None,
                }
            )
        delay = raw.get("frame_delay_ms", 0)
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not 0 <= delay <= 60000:
            raise SimulationConfigError("frame_delay_ms must be a number from 0 to 60000")
        camera_error = raw.get("camera_error")
        if camera_error is True:
            camera_error = "simulated camera error"
        elif camera_error is False or camera_error is None:
            camera_error = None
        elif not isinstance(camera_error, str) or not camera_error.strip():
            raise SimulationConfigError("camera_error must be a boolean, non-empty string, or null")
        else:
            camera_error = camera_error.strip()
        camera_stalled = raw.get("camera_stalled", False)
        if not isinstance(camera_stalled, bool):
            raise SimulationConfigError("camera_stalled must be a boolean")
        return {
            "frames": frames,
            "frame_delay_ms": int(delay),
            "camera_error": camera_error,
            "camera_stalled": camera_stalled,
        }

    def configure(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise SimulationConfigError("simulation configuration must be an object")
        unknown = set(payload) - _CONFIG_KEYS
        if unknown:
            raise SimulationConfigError(f"unknown configuration field(s): {', '.join(sorted(unknown))}")
        has_nested_scenario = "scenario" in payload
        nested = payload.get("scenario")
        if has_nested_scenario:
            scenario_payload = nested
        else:
            with self._simulation_lock:
                current_scenario = deepcopy(self._scenario)
            scenario_payload = {
                **current_scenario,
                **{key: value for key, value in payload.items() if key in _SCENARIO_KEYS},
            }
        scenario = self._validate_scenario(scenario_payload)
        if has_nested_scenario:
            extras = set(payload) - {"scenario", "serial_connected", "fail_commands"}
            if extras:
                raise SimulationConfigError("scenario fields must be nested under scenario")
        serial_connected = payload.get("serial_connected", self._serial_connected)
        serial_connected = self._validate_bool(serial_connected, "serial_connected")
        fail_commands = payload.get("fail_commands", list(self._fail_commands))
        if not isinstance(fail_commands, list):
            raise SimulationConfigError("fail_commands must be an array")
        normalized_commands = set()
        for command in fail_commands:
            if not isinstance(command, str) or command.upper() not in _COMMANDS:
                raise SimulationConfigError("fail_commands may only contain LOCK, UNLOCK, START, or STOP")
            normalized_commands.add(command.upper())
        with self._simulation_lock:
            self._scenario = scenario
            self._serial_connected = serial_connected
            self._fail_commands = normalized_commands
        with self._lock:
            if serial_connected and not normalized_commands:
                self._serial_error = None
            if not scenario["camera_error"] and not scenario["camera_stalled"]:
                self._camera_error = None
        if not serial_connected:
            self._close_serial()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._simulation_lock:
            scenario = deepcopy(self._scenario)
            serial_connected = self._serial_connected
            fail_commands = sorted(self._fail_commands)
            log = deepcopy(self._command_log)
        with self._lock:
            serial_error = self._serial_error
        commands = [entry["command"] for entry in log]
        return {
            "ready": bool(
                not self._closed
                and serial_connected
                and not serial_error
                and not fail_commands
                and not scenario["camera_error"]
                and not scenario["camera_stalled"]
            ),
            "serial_connected": serial_connected,
            "serial_error": serial_error,
            "fail_commands": fail_commands,
            "frames": scenario["frames"],
            "frame_delay_ms": scenario["frame_delay_ms"],
            "camera_error": scenario["camera_error"],
            "camera_stalled": scenario["camera_stalled"],
            "command_log": log,
            "commands": commands,
            "scenario": scenario,
        }

    def get_command_log(self) -> list[dict[str, Any]]:
        with self._simulation_lock:
            return deepcopy(self._command_log)

    @property
    def command_log(self) -> list[dict[str, Any]]:
        return self.get_command_log()

    @property
    def commands(self) -> list[str]:
        with self._simulation_lock:
            return [entry["command"] for entry in self._command_log]

    def clear_command_log(self) -> None:
        with self._simulation_lock:
            self._command_log.clear()

    def status(self) -> dict[str, Any]:
        base = super().status()
        simulation = self.snapshot()
        if simulation["camera_error"]:
            simulation_error = simulation["camera_error"]
        elif simulation["camera_stalled"]:
            simulation_error = "simulated camera capture is stalled"
        elif simulation["serial_error"]:
            simulation_error = simulation["serial_error"]
        elif simulation["fail_commands"]:
            simulation_error = f"simulated command failure: {', '.join(simulation['fail_commands'])}"
        elif not simulation["serial_connected"]:
            simulation_error = "simulated ESP32 is disconnected"
        else:
            simulation_error = None
        base.update(
            ready=simulation["ready"],
            hardware="closed" if self._closed else ("ready" if simulation["ready"] else "degraded"),
            dependencies_loaded=True,
            model_loaded=True,
            camera_open=bool(self._camera is not None),
            esp32_connected=bool(simulation["serial_connected"] and not simulation["serial_error"]),
            serial_port=(
                "simulated://esp32"
                if simulation["serial_connected"] and not simulation["serial_error"]
                else None
            ),
            error=simulation_error,
            simulation=simulation,
        )
        return base

    def _get_face_engine(self) -> _SimulatedFaceEngine:
        return self._simulated_face_engine

    def _load_modules(self) -> dict[str, Any]:
        return {"simulation": True}

    def _ensure_model(self) -> object:
        return self

    def _open_camera(self, owner_id: str | None = None) -> _SimulatedCamera:
        with self._simulation_lock:
            camera_error = self._scenario["camera_error"]
        if camera_error:
            self._record_error(camera_error, "camera")
            raise RuntimeError(camera_error)
        camera = _SimulatedCamera(self, owner_id)
        with self._lock:
            if self._closed:
                raise RuntimeError("Pi runtime is shutting down")
            self._camera = camera
            self._camera_owner_id = owner_id
            self._camera_error = None
        return camera

    def _capture_bgr(self, camera: _SimulatedCamera) -> dict[str, Any] | None:
        return camera.capture_array()

    @staticmethod
    def _enroll_sample_interval() -> float:
        # Scripted frame delay is the simulation's explicit pacing control;
        # retaining the production half-second pause would make local
        # enrollment needlessly slow and obscure the worker behavior.
        return 0.0

    def _ensure_serial(self) -> _SimulatedSerial:
        with self._simulation_lock:
            connected = self._serial_connected
        if not connected:
            self._record_error("simulated ESP32 is disconnected", "serial")
            raise RuntimeError("simulated ESP32 is disconnected")
        with self._lock:
            if self._serial is None or not getattr(self._serial, "is_open", True):
                self._serial = _SimulatedSerial()
                self._serial_port = "simulated://esp32"
                self._serial_error = None
            return self._serial

    def _send_command(self, command: str, connect: bool = True) -> bool:
        command = str(command).upper()
        if command not in _COMMANDS:
            raise ValueError(f"unsupported simulated command: {command}")
        ok = True
        error = None
        try:
            connection = self._ensure_serial() if connect else self._serial
            if connection is None:
                raise RuntimeError("simulated ESP32 is disconnected")
            with self._serial_lock:
                connection.write((command + "\n").encode("ascii"))
                connection.flush()
            with self._simulation_lock:
                if command in self._fail_commands:
                    raise RuntimeError(f"simulated command {command} failure")
        except Exception as exc:
            ok = False
            error = str(exc)
            self._record_error(f"ESP32 command {command} failed: {error}", "serial")
            self._close_serial()
        else:
            with self._lock:
                self._serial_error = None
        with self._simulation_lock:
            self._command_log.append(
                {"command": command, "ok": ok, "error": error, "timestamp": int(time.time() * 1000)}
            )
        return ok

    def close(self) -> None:
        # PiRuntime initializes physical resources lazily.  A simulation still
        # represents an available actuator from the first status call, so make
        # the fake serial endpoint visible to the inherited shutdown guard and
        # exercise its STOP/LOCK path as well.
        with self._simulation_lock:
            connected = self._serial_connected
        if connected and not self._closed:
            try:
                self._ensure_serial()
            except Exception:
                pass
        super().close()

    def reset(self) -> dict[str, Any]:
        result = self.force_lock(reason="simulation_reset")
        # The inherited worker publishes a terminal view just before its
        # finally block releases camera ownership.  Wait briefly so callers
        # can configure and start the next scripted session immediately after
        # reset, while still bounding the wait if a custom seam is broken.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with self._lock:
                active = self._active_session_id
            if active is None:
                break
            time.sleep(0.01)
        with self._simulation_lock:
            self._scenario = self._default_scenario()
        snapshot = self.snapshot()
        snapshot.update({"ok": bool(result.get("ok")), "reset": result})
        return snapshot


__all__ = ["SimulatedPiRuntime", "SimulationConfigError"]
