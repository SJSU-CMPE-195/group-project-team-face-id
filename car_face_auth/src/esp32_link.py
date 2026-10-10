"""Read face-authentication triggers from the ESP32 over the USB serial link.

The ESP32 asks for a scan with one line, ``START_AUTH UNLOCK`` or
``START_AUTH IGNITION``.  Every other line it prints (``Locked``,
``Engine ON``, ...) is human-readable status and is ignored here.  Replies go
back through the runtime's normal command writer, so this module only reads.
"""

from __future__ import annotations

import threading
from typing import Any


START_AUTH = "START_AUTH"
AUTH_REPLIES = frozenset(("AUTH_PENDING", "AUTHORIZED", "DENIED", "BUSY", "ERROR"))
_PURPOSES = {"UNLOCK": "unlock", "IGNITION": "ignition"}

DEFAULT_RETRY_SECONDS = 1.0
MAX_RETRY_SECONDS = 10.0


def parse_line(raw: bytes) -> str | None:
    """Return the scan purpose for a ``START_AUTH`` line, otherwise ``None``."""

    try:
        text = raw.decode("ascii").strip().upper()
    except (AttributeError, UnicodeDecodeError):
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] != START_AUTH:
        return None
    return _PURPOSES.get(parts[1])


class Esp32Link:
    """Background reader that forwards ESP32 triggers to the runtime.

    The runtime owns the serial port.  The reader never holds the runtime's
    serial write lock while blocked in ``readline``; pyserial supports one
    reader thread alongside a writer.
    """

    def __init__(
        self,
        runtime: Any,
        *,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        max_retry_seconds: float = MAX_RETRY_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="esp32-link",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self) -> None:
        delay = self._retry_seconds
        last_connection = None
        while not self._stop.is_set():
            try:
                connection = self._runtime.esp32_link_connection()
            except Exception:
                connection = None
            if connection is None:
                self._stop.wait(delay)
                delay = min(delay * 2, self._max_retry_seconds)
                continue
            delay = self._retry_seconds
            if connection is not last_connection:
                # Opening the port resets the ESP32, which boots unlocked.
                last_connection = connection
                self._runtime.sync_esp32_state()
            try:
                raw = connection.readline()
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._runtime.esp32_link_failed(connection, exc)
                self._stop.wait(delay)
                continue
            purpose = parse_line(raw)
            if purpose is None or self._stop.is_set():
                continue
            try:
                self._runtime.handle_esp32_trigger(purpose)
            except Exception as exc:
                self._runtime.esp32_link_failed(None, exc)
