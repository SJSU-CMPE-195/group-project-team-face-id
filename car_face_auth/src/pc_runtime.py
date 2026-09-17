"""Real PC-webcam recognition with simulated PC actuators.

Session validation, face matching, permissions, and SQLite writes are inherited
from the Pi runtime. Only hardware dependencies and actuator transport differ.
"""

from __future__ import annotations

import importlib
from typing import Any

from .camera_capture import CAMERA_RECOVERY_HINT
from .pi_runtime import PiRuntime


class PcRuntime(PiRuntime):
    """Run the production recognition flow with a PC webcam."""

    camera_source = "pc_webcam"
    camera_label = "PC webcam"

    def initialize(self) -> dict[str, Any]:
        """Load CPU recognition before advertising a usable device."""
        self._ensure_model()
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            status = super().status()
            ready = (
                self._model is not None
                and self._camera_error is None
                and not self._closed
            )
            hardware = "ready" if ready else "not_initialized"
            if self._hardware_error or self._model_error or self._camera_error:
                hardware = "unavailable"
            if self._closed:
                hardware = "closed"
            status.update(
                ready=ready,
                hardware=hardware,
                runtime_mode="pc",
                actuator_mode="simulated",
                camera_open=self._camera is not None,
                esp32_connected=False,
                serial_port=None,
            )
            return status

    def _load_modules(self) -> dict[str, Any]:
        with self._lock:
            if self._modules is not None:
                return self._modules
            try:
                modules = {
                    "cv2": importlib.import_module("cv2"),
                    "FaceAnalysis": importlib.import_module(
                        "insightface.app"
                    ).FaceAnalysis,
                }
            except Exception as exc:
                message = f"PC recognition dependencies unavailable: {exc}"
                self._record_error(message, "hardware")
                raise RuntimeError(message) from exc
            self._modules = modules
            self._hardware_error = None
            return modules

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
            cv2 = self._load_modules()["cv2"]
            camera = cv2.VideoCapture(0)
            if not camera.isOpened():
                raise RuntimeError("camera index 0 could not be opened")
        except Exception as exc:
            if camera is not None:
                camera.release()
            message = f"PC webcam unavailable: {exc}. {CAMERA_RECOVERY_HINT}"
            self._record_error(message, "camera")
            raise RuntimeError(message) from exc

        with self._lock:
            self._camera = camera
            self._camera_owner_id = owner_id
            self._camera_error = None
        return camera

    def _capture_bgr(self, camera: Any) -> Any:
        with self._camera_io_lock:
            captured, frame = camera.read()
        if not captured or frame is None:
            message = "PC webcam did not return a frame"
            self._record_error(message, "camera")
            raise RuntimeError("camera did not return a frame")
        return frame

    def _send_command(self, command: str, connect: bool = True) -> bool:
        # No physical actuator is attached in PC mode. Recognition and the
        # inherited state transitions still run against the canonical DB.
        if command not in {"UNLOCK", "LOCK", "START", "STOP"}:
            self._record_error("Unknown simulated actuator command", "serial")
            return False
        self._log("simulated_actuator", "ok", command)
        return True
