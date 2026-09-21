"""Run one application behind LAN TLS and a separate loopback dashboard."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
import queue
import socket
import threading
import time

from cheroot.ssl.pyopenssl import pyOpenSSLAdapter
from cheroot.server import HTTPConnection, HTTPRequest
from cheroot.wsgi import Server
from OpenSSL import SSL


MAX_REQUEST_HEADER_BYTES = 16 * 1024
MAX_REQUEST_BODY_BYTES = 9 * 1024 * 1024


@dataclass(frozen=True)
class _ServerLimits:
    threads: int
    accepted_connections: int
    backlog_connections: int
    keep_alive_connections: int
    timeout_seconds: float
    handshake_deadline_seconds: float
    header_deadline_seconds: float
    body_deadline_seconds: float
    shutdown_timeout_seconds: float


_LAN_LIMITS = _ServerLimits(
    threads=8,
    accepted_connections=8,
    backlog_connections=16,
    keep_alive_connections=8,
    timeout_seconds=5,
    handshake_deadline_seconds=5,
    header_deadline_seconds=5,
    body_deadline_seconds=15,
    shutdown_timeout_seconds=5,
)
_DASHBOARD_LIMITS = _ServerLimits(
    threads=4,
    accepted_connections=4,
    backlog_connections=8,
    keep_alive_connections=4,
    timeout_seconds=5,
    handshake_deadline_seconds=5,
    header_deadline_seconds=5,
    body_deadline_seconds=15,
    shutdown_timeout_seconds=5,
)


class _DeadlineReader:
    """Put absolute deadlines around inbound request phases only."""

    def __init__(self, stream, connection, limits: _ServerLimits):
        self._stream = stream
        self._connection = connection
        self._limits = limits
        self._lock = threading.Lock()
        self._timer = None
        self._generation = 0
        self._phase = None
        self._body_remaining = None
        self.expired = False
        self.start_headers()

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def _tls_handshake_finished(self) -> bool:
        return not isinstance(self._connection, SSL.Connection) or bool(
            self._connection.get_finished()
        )

    def _arm(self, phase: str, seconds: float) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            if self._timer is not None:
                self._timer.cancel()
            self._phase = phase
            self.expired = False
            timer = threading.Timer(seconds, self._expire, args=(generation,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _cancel(self) -> None:
        with self._lock:
            self._generation += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._phase = None

    def _expire(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self.expired = True
            self._timer = None
        raw_socket = (
            self._connection._socket
            if isinstance(self._connection, SSL.Connection)
            else self._connection
        )
        with suppress(OSError):
            raw_socket.shutdown(socket.SHUT_RDWR)

    def start_headers(self) -> None:
        self._body_remaining = None
        if self._tls_handshake_finished():
            self._arm("headers", self._limits.header_deadline_seconds)
        else:
            self._arm("handshake", self._limits.handshake_deadline_seconds)

    def start_body(self, content_length: int | None) -> None:
        self._body_remaining = content_length
        if content_length == 0:
            self._cancel()
            return
        self._arm("body", self._limits.body_deadline_seconds)

    def finish_body(self) -> None:
        self._cancel()

    def _after_read(self, result, operation: str) -> None:
        if self.expired:
            raise socket.timeout("timed out")
        if self._phase == "handshake" and self._tls_handshake_finished():
            self._arm("headers", self._limits.header_deadline_seconds)
        if self._phase != "body":
            return
        if self._body_remaining is None:
            if operation == "readline":
                chunk_size = (result or b"").strip().split(b";", 1)[0]
                with suppress(ValueError):
                    if int(chunk_size, 16) == 0:
                        self._cancel()
            return
        bytes_read = result if isinstance(result, int) else len(result or b"")
        self._body_remaining -= bytes_read
        if self._body_remaining <= 0:
            self._cancel()

    def _read(self, operation: str, method, *args, **kwargs):
        while True:
            if self.expired:
                raise socket.timeout("timed out")
            try:
                result = method(*args, **kwargs)
                break
            except (SSL.WantReadError, SSL.WantWriteError):
                time.sleep(0.01)
            except SSL.Error:
                if self.expired:
                    raise socket.timeout("timed out") from None
                raise
            except OSError:
                if self.expired:
                    raise socket.timeout("timed out") from None
                raise
        self._after_read(result, operation)
        return result

    def read(self, *args, **kwargs):
        return self._read("read", self._stream.read, *args, **kwargs)

    def read1(self, *args, **kwargs):
        return self._read("read", self._stream.read1, *args, **kwargs)

    def readline(self, *args, **kwargs):
        return self._read("readline", self._stream.readline, *args, **kwargs)

    def readinto(self, *args, **kwargs):
        return self._read("read", self._stream.readinto, *args, **kwargs)

    def readinto1(self, *args, **kwargs):
        return self._read("read", self._stream.readinto1, *args, **kwargs)

    def readlines(self, *args, **kwargs):
        return self._read("read", self._stream.readlines, *args, **kwargs)

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def close(self) -> None:
        self._cancel()
        self._stream.close()


class _DeadlineRequest(HTTPRequest):
    def respond(self):
        content_length = None if self.chunked_read else int(
            self.inheaders.get(b"Content-Length", 0)
        )
        self.conn.rfile.start_body(content_length)
        super().respond()

    def send_headers(self):
        try:
            return super().send_headers()
        finally:
            # From this boundary onward only the response may remain active.
            self.conn.rfile.finish_body()


class _TlsAwareConnection(HTTPConnection):
    """Avoid writing an HTTP timeout before a TLS handshake exists."""

    RequestHandlerClass = _DeadlineRequest

    def __init__(self, server, sock, makefile):
        super().__init__(server, sock, makefile)
        self.rfile = _DeadlineReader(self.rfile, sock, server.request_limits)

    def communicate(self):
        self.rfile.start_headers()
        return super().communicate()

    def _conditional_error(self, req, response):
        if self.rfile.expired or (
            isinstance(self.socket, SSL.Connection)
            and not self.socket.get_finished()
        ):
            return
        super()._conditional_error(req, response)

    def _handle_no_ssl(self, req):
        super()._handle_no_ssl(req)
        # Cheroot otherwise leaves this rejected plaintext socket to GC.
        self.linger = False


class _BoundedServer(Server):
    """Drop overflow immediately instead of growing Cheroot's 503 queue."""

    ConnectionClass = _TlsAwareConnection

    def process_conn(self, conn):
        try:
            self.requests.put(conn)
        except queue.Full:
            with suppress(OSError, SSL.Error):
                conn.close()

    @staticmethod
    def bind_socket(socket_, bind_addr):
        try:
            return Server.bind_socket(socket_, bind_addr)
        except BaseException:
            socket_.close()
            raise


def _tls_adapter(identity_path: Path) -> pyOpenSSLAdapter:
    try:
        context = SSL.Context(SSL.TLS_SERVER_METHOD)
        context.set_min_proto_version(SSL.TLS1_2_VERSION)
        context.set_options(SSL.OP_NO_COMPRESSION)
        context.use_privatekey_file(str(identity_path))
        context.use_certificate_file(str(identity_path))
        context.check_privatekey()
    except (OSError, SSL.Error) as exc:
        raise RuntimeError(
            f"TLS identity cannot be loaded by the HTTPS server: {identity_path}"
        ) from exc

    adapter = pyOpenSSLAdapter(str(identity_path), str(identity_path))
    adapter.context = context
    return adapter


def _build_server(
    bind_addr: tuple[str, int],
    app,
    *,
    limits: _ServerLimits,
    tls_identity_path: Path | None = None,
) -> _BoundedServer:
    server = _BoundedServer(
        bind_addr,
        app,
        numthreads=limits.threads,
        max=limits.threads,
        request_queue_size=limits.backlog_connections,
        timeout=limits.timeout_seconds,
        shutdown_timeout=limits.shutdown_timeout_seconds,
        accepted_queue_size=limits.accepted_connections,
        accepted_queue_timeout=0,
    )
    server.keep_alive_conn_limit = limits.keep_alive_connections
    server.request_limits = limits
    server.max_request_header_size = MAX_REQUEST_HEADER_BYTES
    server.max_request_body_size = MAX_REQUEST_BODY_BYTES
    if tls_identity_path is not None:
        server.ssl_adapter = _tls_adapter(tls_identity_path)
    return server


def _prepare(server: _BoundedServer) -> None:
    try:
        server.prepare()
    except BaseException:
        if hasattr(server, "_connections"):
            # Cheroot's stop() otherwise returns early during partial startup.
            server.ready = True
            server.stop()
        else:
            sock = getattr(server, "socket", None)
            if sock is not None:
                sock.close()
                server.socket = None
        raise


@contextmanager
def wireless_servers(
    app,
    *,
    host: str,
    port: int,
    dashboard_port: int,
    tls_identity_path: Path,
):
    if port == dashboard_port:
        raise ValueError("HTTPS and local dashboard ports must be different.")

    https_server = _build_server(
        (host, port),
        app,
        limits=_LAN_LIMITS,
        tls_identity_path=tls_identity_path,
    )
    dashboard_server = _build_server(
        ("127.0.0.1", dashboard_port),
        app,
        limits=_DASHBOARD_LIMITS,
    )

    cleanup = ExitStack()
    dashboard_thread = None
    try:
        _prepare(https_server)
        cleanup.callback(https_server.stop)
        _prepare(dashboard_server)
        cleanup.callback(dashboard_server.stop)
        dashboard_thread = threading.Thread(
            target=dashboard_server.serve,
            name="bass-local-dashboard",
            daemon=True,
        )
        dashboard_thread.start()
        yield https_server
    finally:
        try:
            cleanup.close()
        finally:
            if dashboard_thread is not None:
                dashboard_thread.join(
                    timeout=_DASHBOARD_LIMITS.shutdown_timeout_seconds + 1,
                )
                if dashboard_thread.is_alive():
                    raise RuntimeError("Local dashboard server did not stop.")
