"""Isolated connection-limit checks for the production WSGI/TLS server."""

from __future__ import annotations

from contextlib import contextmanager, suppress
import http.client
from pathlib import Path
import queue
import socket
import ssl
import tempfile
import threading
import time
import unittest

from wireless.config import load_or_create_device_config
from wireless.server import (
    MAX_REQUEST_BODY_BYTES,
    MAX_REQUEST_HEADER_BYTES,
    _BoundedServer,
    _ServerLimits,
    _build_server,
    _prepare,
)
from wireless.tls import load_or_create_tls_identity


def simple_app(environ, start_response):
    if environ["PATH_INFO"] == "/read-body":
        while environ["wsgi.input"].read(1):
            pass
    if environ["PATH_INFO"] == "/stream":
        start_response("200 OK", [("Content-Type", "application/octet-stream")])

        def stream():
            yield b"one"
            time.sleep(0.5)
            yield b"two"

        return stream()
    body = f"{environ['wsgi.url_scheme']}:{environ['SERVER_PORT']}".encode()
    start_response(
        "200 OK",
        [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))],
    )
    return [body]


class WirelessServerLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="bass-server-limits-")
        cls.addClassCleanup(cls.folder.cleanup)
        root = Path(cls.folder.name)
        config_path = root / "device.json"
        config = load_or_create_device_config(config_path)
        cls.identity = load_or_create_tls_identity(config_path, config.device_id)

    def limits(
        self,
        *,
        threads=2,
        accepted_connections=1,
        timeout_seconds=0.5,
        deadline_seconds=None,
    ):
        deadline_seconds = deadline_seconds or timeout_seconds
        return _ServerLimits(
            threads=threads,
            accepted_connections=accepted_connections,
            backlog_connections=2,
            keep_alive_connections=1,
            timeout_seconds=timeout_seconds,
            handshake_deadline_seconds=deadline_seconds,
            header_deadline_seconds=deadline_seconds,
            body_deadline_seconds=deadline_seconds,
            shutdown_timeout_seconds=1,
        )

    @contextmanager
    def running_server(self, *, limits=None):
        server = _build_server(
            ("127.0.0.1", 0),
            simple_app,
            limits=limits or self.limits(),
            tls_identity_path=self.identity.path,
        )
        _prepare(server)
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        try:
            yield server, server.bind_addr[1]
        finally:
            server.stop()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "server shutdown did not finish")

    def https(self, port, *, timeout=2):
        context = ssl.create_default_context(cafile=str(self.identity.path))
        context.check_hostname = False
        return http.client.HTTPSConnection(
            "127.0.0.1",
            port,
            context=context,
            timeout=timeout,
        )

    def tls_socket(self, port, *, timeout=2):
        context = ssl.create_default_context(cafile=str(self.identity.path))
        context.check_hostname = False
        raw = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        return context.wrap_socket(raw, server_hostname="localhost")

    def drip_until_closed(self, connection, payload=b"x"):
        started = time.monotonic()
        offset = 0
        while time.monotonic() - started < 1:
            try:
                connection.sendall(payload[offset % len(payload) :][:1])
                offset += 1
            except OSError:
                return time.monotonic() - started
            time.sleep(0.08)
            connection.settimeout(0.01)
            try:
                response = connection.recv(1)
            except (OSError, socket.timeout):
                continue
            if not response:
                return time.monotonic() - started
        self.fail("slow request remained open past its absolute deadline")

    def test_idle_tls_client_does_not_block_other_handshakes(self):
        with self.running_server() as (_server, port):
            idle = socket.create_connection(("127.0.0.1", port), timeout=1)
            self.addCleanup(idle.close)
            connection = self.https(port)
            try:
                started = time.monotonic()
                connection.request("GET", "/")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), f"https:{port}".encode())
                self.assertLess(time.monotonic() - started, 1)
            finally:
                connection.close()

    def test_handshake_and_incomplete_headers_time_out(self):
        limits = self.limits(timeout_seconds=0.25)
        with self.running_server(limits=limits) as (_server, port):
            idle = socket.create_connection(("127.0.0.1", port), timeout=1)
            idle.settimeout(1)
            started = time.monotonic()
            self.assertEqual(idle.recv(1), b"")
            self.assertLess(time.monotonic() - started, 1)
            idle.close()

            connection = self.tls_socket(port)
            try:
                connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost")
                response = connection.recv(4096)
                self.assertTrue(
                    not response or b"408 Request Timeout" in response,
                    response,
                )
            finally:
                connection.close()

    def test_client_hello_drip_cannot_extend_handshake_deadline(self):
        limits = self.limits(timeout_seconds=0.2, deadline_seconds=0.35)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        incoming = ssl.MemoryBIO()
        outgoing = ssl.MemoryBIO()
        tls = context.wrap_bio(incoming, outgoing, server_hostname="localhost")
        with self.assertRaises(ssl.SSLWantReadError):
            tls.do_handshake()
        client_hello = outgoing.read()

        with self.running_server(limits=limits) as (_server, port):
            connection = socket.create_connection(("127.0.0.1", port), timeout=1)
            try:
                elapsed = self.drip_until_closed(connection, client_hello)
                self.assertGreater(elapsed, 0.2)
                self.assertLess(elapsed, 0.8)
            finally:
                connection.close()

    def test_header_byte_drip_cannot_extend_header_deadline(self):
        limits = self.limits(timeout_seconds=0.2, deadline_seconds=0.35)
        with self.running_server(limits=limits) as (_server, port):
            connection = self.tls_socket(port)
            try:
                connection.sendall(b"GET / HTTP/1.1\r\n")
                elapsed = self.drip_until_closed(connection)
                self.assertGreater(elapsed, 0.2)
                self.assertLess(elapsed, 0.8)
            finally:
                connection.close()

    def test_body_byte_drip_cannot_extend_body_deadline(self):
        limits = self.limits(timeout_seconds=0.2, deadline_seconds=0.35)
        with self.running_server(limits=limits) as (_server, port):
            connection = self.tls_socket(port)
            try:
                connection.sendall(
                    b"POST /read-body HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Content-Length: 100\r\n\r\n"
                )
                elapsed = self.drip_until_closed(connection)
                self.assertGreater(elapsed, 0.2)
                self.assertLess(elapsed, 0.8)
            finally:
                connection.close()

            connection = self.tls_socket(port)
            try:
                connection.sendall(
                    b"POST /read-body HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Transfer-Encoding: chunked\r\n\r\n"
                    b"64\r\n"
                )
                elapsed = self.drip_until_closed(connection)
                self.assertGreater(elapsed, 0.2)
                self.assertLess(elapsed, 0.8)
            finally:
                connection.close()

    def test_slow_clients_release_every_worker_after_deadline(self):
        limits = self.limits(
            threads=2,
            accepted_connections=2,
            timeout_seconds=0.2,
            deadline_seconds=0.35,
        )
        with self.running_server(limits=limits) as (_server, port):
            slow_clients = [self.tls_socket(port) for _ in range(limits.threads)]
            try:
                for connection in slow_clients:
                    connection.sendall(b"GET / HTTP/1.1\r\n")
                for _ in range(8):
                    for connection in slow_clients:
                        with suppress(OSError):
                            connection.sendall(b"x")
                    time.sleep(0.08)

                connection = self.https(port)
                try:
                    connection.request("GET", "/")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    response.read()
                finally:
                    connection.close()
            finally:
                for connection in slow_clients:
                    connection.close()

    def test_request_deadlines_do_not_limit_streaming_response(self):
        limits = self.limits(timeout_seconds=0.2, deadline_seconds=0.25)
        for chunked_request in (False, True):
            with self.subTest(chunked_request=chunked_request):
                with self.running_server(limits=limits) as (_server, port):
                    connection = self.https(port, timeout=2)
                    try:
                        if chunked_request:
                            connection.putrequest("GET", "/stream")
                            connection.putheader("Transfer-Encoding", "chunked")
                            connection.endheaders()
                            connection.send(b"0\r\n\r\n")
                        else:
                            connection.request("GET", "/stream")
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), b"onetwo")
                    finally:
                        connection.close()

    def test_header_and_declared_body_limits_are_enforced(self):
        with self.running_server() as (_server, port):
            connection = self.tls_socket(port)
            try:
                connection.sendall(
                    b"GET / HTTP/1.1\r\nX-Large: "
                    + b"x" * MAX_REQUEST_HEADER_BYTES
                    + b"\r\n\r\n"
                )
                self.assertIn(b"413 Request Entity Too Large", connection.recv(4096))
            finally:
                connection.close()

            connection = self.tls_socket(port)
            try:
                request = (
                    "POST / HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    f"Content-Length: {MAX_REQUEST_BODY_BYTES + 1}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                connection.sendall(request)
                self.assertIn(b"413 Request Entity Too Large", connection.recv(4096))
            finally:
                connection.close()

    def test_worker_queue_and_overflow_path_are_bounded(self):
        limits = self.limits(
            threads=2,
            accepted_connections=1,
            timeout_seconds=3,
        )
        with self.running_server(limits=limits) as (server, port):
            clients = []
            try:
                for _ in range(limits.threads):
                    clients.append(
                        socket.create_connection(("127.0.0.1", port), timeout=1)
                    )
                deadline = time.monotonic() + 1
                while server.requests.idle and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(server.requests.idle, 0)

                clients.append(
                    socket.create_connection(("127.0.0.1", port), timeout=1)
                )
                deadline = time.monotonic() + 1
                while server.requests.qsize < limits.accepted_connections and (
                    time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(
                    server.requests.qsize,
                    limits.accepted_connections,
                )

                overflow = socket.create_connection(
                    ("127.0.0.1", port),
                    timeout=1,
                )
                clients.append(overflow)
                overflow.settimeout(1)
                try:
                    self.assertEqual(overflow.recv(1), b"")
                except ConnectionResetError:
                    pass

                self.assertEqual(len(server.requests._threads), limits.threads)
                self.assertLessEqual(
                    server.requests.qsize,
                    limits.accepted_connections,
                )
                self.assertEqual(server._unservicable_conns.qsize(), 0)
            finally:
                for client in clients:
                    client.close()

    def test_overflow_connection_is_closed_without_503_queue(self):
        class FullRequests:
            def put(self, _conn):
                raise queue.Full

        class Connection:
            closed = False

            def close(self):
                self.closed = True

        server = object.__new__(_BoundedServer)
        server.requests = FullRequests()
        connection = Connection()
        server.process_conn(connection)
        self.assertTrue(connection.closed)

    def test_failed_startup_releases_reserved_port(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            reservation.listen()
            port = reservation.getsockname()[1]
            server = _build_server(
                ("127.0.0.1", port),
                simple_app,
                limits=self.limits(),
            )
            with self.assertRaises(OSError):
                _prepare(server)

        with socket.socket() as replacement:
            replacement.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
