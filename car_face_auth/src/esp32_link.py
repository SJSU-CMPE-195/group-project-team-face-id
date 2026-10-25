"""Match ESP32 face requests and acknowledgements on one serial connection."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import threading
from typing import Any


AUTH_REPLIES = frozenset(
    ("AUTH_PENDING", "AUTHORIZED", "UNAUTHORIZED", "DENIED", "BUSY", "ERROR")
)
_PURPOSES = {"UNLOCK": "unlock", "IGNITION": "ignition"}
_REQUEST_ID = re.compile(r"[0-9A-F]{16}\Z")

DEFAULT_RETRY_SECONDS = 1.0
MAX_RETRY_SECONDS = 10.0
AUTH_ACK_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class AuthRequest:
    purpose: str
    request_id: str
    connection: Any


@dataclass
class PendingAck:
    request: AuthRequest
    done: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False


def valid_request_id(value: str) -> bool:
    return bool(_REQUEST_ID.fullmatch(value))


def parse_line(raw: bytes) -> tuple[str, str, str | None] | None:
    """Return (command, request ID, purpose), rejecting incomplete/legacy lines."""

    if not isinstance(raw, bytes) or not raw.endswith(b"\n"):
        return None
    try:
        parts = raw.decode("ascii").strip().upper().split()
    except UnicodeDecodeError:
        return None
    if len(parts) == 3 and parts[0] == "START_AUTH":
        purpose = _PURPOSES.get(parts[1])
        if purpose and valid_request_id(parts[2]):
            return parts[0], parts[2], purpose
    if len(parts) == 2 and parts[0] in {"AUTH_ACK", "AUTH_CANCEL"}:
        if valid_request_id(parts[1]):
            return parts[0], parts[1], None
    return None


class Esp32Link:
    """Keep the reader free to receive ACKs while a scan worker waits for one."""

    def __init__(
        self,
        runtime: Any,
        *,
        retry_seconds: float = DEFAULT_RETRY_SECONDS,
        max_retry_seconds: float = MAX_RETRY_SECONDS,
        ack_timeout: float = AUTH_ACK_TIMEOUT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._retry_seconds = retry_seconds
        self._max_retry_seconds = max_retry_seconds
        self._ack_timeout = ack_timeout
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._connection: Any = None
        self._pending: PendingAck | None = None
        self._cancelled_request_id: str | None = None

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
        self.connection_lost(self._connection)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def connection_lost(self, connection: Any) -> None:
        with self._lock:
            if connection is not self._connection:
                return
            self._connection = None
            self._cancelled_request_id = None
            if self._pending is not None:
                self._pending.accepted = False
                self._pending.done.set()

    def reply(self, request: AuthRequest, reply: str) -> bool:
        with self._lock:
            current = request.connection is self._connection
        if not current or self._stop.is_set():
            return False
        return self._runtime.send_esp32_reply(reply, request)

    def authorize(self, request: AuthRequest) -> bool:
        pending = PendingAck(request)
        with self._lock:
            if (
                self._stop.is_set()
                or request.connection is not self._connection
                or self._pending is not None
                or request.request_id == self._cancelled_request_id
            ):
                return False
            self._pending = pending
        try:
            if not self.reply(request, "AUTHORIZED"):
                return False
            pending.done.wait(self._ack_timeout)
            with self._lock:
                return bool(
                    pending.accepted
                    and request.connection is self._connection
                    and not self._stop.is_set()
                )
        finally:
            with self._lock:
                if self._pending is pending:
                    self._pending = None

    def _receive_result(
        self, connection: Any, request_id: str, accepted: bool
    ) -> None:
        with self._lock:
            # Keep an early cancellation until the worker can observe it, even
            # when it has not installed its ACK wait yet. There is one scan.
            if not accepted and connection is self._connection:
                self._cancelled_request_id = request_id
            pending = self._pending
            if (
                pending is not None
                and pending.request.connection is connection
                and pending.request.request_id == request_id
            ):
                pending.accepted = accepted
                pending.done.set()

    def _run(self) -> None:
        delay = self._retry_seconds
        while not self._stop.is_set():
            connection = None
            try:
                connection = self._runtime.esp32_link_connection()
                if connection is None:
                    self.connection_lost(self._connection)
                    self._stop.wait(delay)
                    delay = min(delay * 2, self._max_retry_seconds)
                    continue
                delay = self._retry_seconds
                with self._lock:
                    changed = connection is not self._connection
                if changed:
                    self.connection_lost(self._connection)
                    with self._lock:
                        self._connection = connection
                    self._runtime.sync_esp32_state()
                raw = connection.readline()
                message = parse_line(raw)
                if message is None or self._stop.is_set():
                    continue
                command, request_id, purpose = message
                if command == "START_AUTH":
                    request = AuthRequest(purpose, request_id, connection)
                    self._runtime.handle_esp32_trigger(request)
                else:
                    # Wake the worker before cancellation takes the actuation lock.
                    self._receive_result(
                        connection, request_id, command == "AUTH_ACK"
                    )
                    if command == "AUTH_CANCEL":
                        self._runtime.cancel_esp32_scan(request_id, connection)
            except Exception as exc:
                if self._stop.is_set():
                    return
                self.connection_lost(connection)
                self._runtime.esp32_link_failed(connection, exc)
                self._stop.wait(delay)
                delay = min(delay * 2, self._max_retry_seconds)
