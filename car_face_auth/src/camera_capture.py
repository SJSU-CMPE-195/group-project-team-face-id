"""Bounded, session-scoped camera capture for recognition and live preview."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable


CAPTURE_FPS = 20.0
FRAME_STALE_SECONDS = 2.0
CAMERA_RECOVERY_HINT = (
    "Another app (such as Zoom) may be using the camera. "
    "Close apps using it, check its connection and permissions, then retry."
)


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    frame_id: int
    bgr: Any
    jpeg: bytes | None
    captured_at: float


class SessionCameraCapture:
    """Continuously retain only the latest frame for one camera session."""

    def __init__(
        self,
        session_id: str,
        camera: Any,
        capture_bgr: Callable[[Any], Any],
        encode_jpeg: Callable[[Any], bytes | None],
        *,
        initial_frame_id: int = 0,
    ):
        self.session_id = session_id
        self._camera = camera
        self._capture_bgr = capture_bgr
        self._encode_jpeg = encode_jpeg
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._frame_id = initial_frame_id
        self._latest: CapturedFrame | None = None
        self._last_capture_at = 0.0
        self._started_at = time.monotonic()
        self._running = False
        self._error: str | None = None
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"camera-{session_id}",
        )

    @property
    def frame_id(self) -> int:
        with self._condition:
            return self._frame_id

    @property
    def active(self) -> bool:
        with self._condition:
            return bool(
                self._running
                and not self._stop_event.is_set()
                and not self._error
            )

    def start(self) -> None:
        with self._condition:
            self._running = True
        self._thread.start()

    def request_stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._latest = None
            self._condition.notify_all()

    def stop(self, timeout: float) -> bool:
        self.request_stop()
        if self._thread is threading.current_thread():
            return False
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def join(self) -> None:
        if self._thread is not threading.current_thread():
            self._thread.join()

    def wait_for_newer(
        self,
        frame_id: int,
        timeout: float,
    ) -> CapturedFrame | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                if self._latest and self._latest.frame_id > frame_id:
                    return self._latest
                if self._error or not self._running or self._stop_event.is_set():
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def snapshot(self, max_age: float = FRAME_STALE_SECONDS) -> CapturedFrame | None:
        with self._condition:
            if self._latest and time.monotonic() - self._latest.captured_at <= max_age:
                return self._latest
            self._latest = None
            return None

    def failure(self, max_age: float = FRAME_STALE_SECONDS) -> str | None:
        with self._condition:
            if self._error:
                return f"{self._error}. {CAMERA_RECOVERY_HINT}"
            if self._stop_event.is_set():
                return None
            if not self._running:
                return f"camera capture stopped unexpectedly. {CAMERA_RECOVERY_HINT}"
            last_activity = self._last_capture_at or self._started_at
            if time.monotonic() - last_activity > max_age:
                return f"camera capture stalled. {CAMERA_RECOVERY_HINT}"
            return None

    def _run(self) -> None:
        interval = 1.0 / CAPTURE_FPS
        next_capture_at = time.monotonic()
        try:
            while not self._stop_event.is_set():
                frame = self._capture_bgr(self._camera)
                if self._stop_event.is_set():
                    break
                captured_at = time.monotonic()
                try:
                    jpeg = self._encode_jpeg(frame)
                except Exception:
                    jpeg = None
                if self._stop_event.is_set():
                    break
                with self._condition:
                    self._frame_id += 1
                    self._last_capture_at = captured_at
                    self._latest = CapturedFrame(
                        frame_id=self._frame_id,
                        bgr=frame,
                        jpeg=jpeg,
                        captured_at=captured_at,
                    )
                    self._condition.notify_all()
                next_capture_at += interval
                delay = next_capture_at - time.monotonic()
                if delay <= 0:
                    next_capture_at = time.monotonic()
                    continue
                self._stop_event.wait(delay)
        except Exception as exc:
            with self._condition:
                self._error = str(exc) or exc.__class__.__name__
        finally:
            with self._condition:
                self._running = False
                self._condition.notify_all()
