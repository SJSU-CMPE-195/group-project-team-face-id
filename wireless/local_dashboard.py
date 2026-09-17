"""Loopback-only dashboard files and authenticated API bridge."""

from __future__ import annotations

import json
import mimetypes
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

from werkzeug.wrappers import Response
from werkzeug.wsgi import wrap_file


LOCAL_API_PREFIX = "/local/wireless/api/"
LOCAL_PAIRING_PATH = "/local/pairing-qr"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_BACKEND_PATHS = ("/api", "/health", "/ready", "/sim")

StartResponse = Callable[[str, list[tuple[str, str]], object | None], object]
WsgiApp = Callable[[dict, StartResponse], Iterable[bytes]]


def _loopback_peer(environ: dict) -> bool:
    try:
        address = ip_address(environ.get("REMOTE_ADDR", ""))
    except ValueError:
        return False
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def _request_origin(environ: dict, expected_port: int) -> str | None:
    scheme = environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST", "")
    try:
        parsed = urlsplit(f"{scheme}://{host}")
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    if (
        scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or port != expected_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    return f"{scheme}://{host}"


def _origin_of(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None
    host = parsed.hostname
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host if port == default_port else f"{host}:{port}"
    if ":" in host:
        authority = f"[{host}]" if port == default_port else f"[{host}]:{port}"
    return f"{parsed.scheme}://{authority}"


def trusted_dashboard_request(environ: dict, expected_port: int) -> bool:
    """Validate a direct local browser request without forwarded headers."""

    if not _loopback_peer(environ):
        return False
    expected_origin = _request_origin(environ, expected_port)
    if expected_origin is None:
        return False

    origin = environ.get("HTTP_ORIGIN", "")
    if origin and origin != expected_origin:
        return False
    referer = environ.get("HTTP_REFERER", "")
    if referer and _origin_of(referer) != expected_origin:
        return False

    fetch_site = environ.get("HTTP_SEC_FETCH_SITE", "")
    if fetch_site:
        return fetch_site in {"same-origin", "none"}
    return bool(referer)


def trusted_pairing_request(environ: dict, expected_port: int) -> bool:
    """Apply the dashboard's same-origin boundary to its pairing QR."""

    return trusted_dashboard_request(environ, expected_port)


def _json_response(message: str, status: int) -> Response:
    body = json.dumps({"ok": False, "error": message}) + "\n"
    response = Response(body, status=status, content_type="application/json")
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _local_start_response(start_response: StartResponse) -> StartResponse:
    def sanitized(status, headers, exc_info=None):
        filtered = [
            (name, value)
            for name, value in headers
            if not name.lower().startswith("access-control-")
            and name.lower() != "cache-control"
        ]
        filtered.append(("Cache-Control", "no-store"))
        return start_response(status, filtered, exc_info)

    return sanitized


class LocalDashboardMiddleware:
    """Serve the built UI locally and bridge it to the existing API app."""

    def __init__(
        self,
        application: WsgiApp,
        *,
        dist_root: Path,
        pairing_key: str,
        port: int,
    ) -> None:
        self._application = application
        self._dist_root = dist_root.resolve()
        self._pairing_key = pairing_key
        self._port = port

    def __call__(self, environ: dict, start_response: StartResponse):
        path = environ.get("PATH_INFO", "")
        if path.startswith(LOCAL_API_PREFIX):
            return self._bridge_api(environ, start_response)
        if path == LOCAL_PAIRING_PATH or self._is_backend_path(path):
            return self._application(environ, start_response)
        if path.startswith("/local/"):
            response = _json_response("local route not found", 404)
            return response(environ, start_response)
        return self._serve_dashboard(environ, start_response)

    @staticmethod
    def _is_backend_path(path: str) -> bool:
        return any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in _BACKEND_PATHS
        )

    def _bridge_api(self, environ: dict, start_response: StartResponse):
        if not trusted_dashboard_request(environ, self._port):
            response = _json_response(
                "Open this dashboard through localhost on the host computer.",
                403,
            )
            return response(environ, start_response)

        bridged = environ.copy()
        path = environ["PATH_INFO"][len("/local/wireless") :]
        bridged["PATH_INFO"] = path
        if not path.startswith("/api/"):
            response = _json_response("local API route not found", 404)
            return response(environ, start_response)
        bridged["HTTP_AUTHORIZATION"] = f"Bearer {self._pairing_key}"
        return self._application(bridged, _local_start_response(start_response))

    def _serve_dashboard(self, environ: dict, start_response: StartResponse):
        if not trusted_dashboard_request(environ, self._port):
            response = _json_response(
                "Open this dashboard through localhost on the host computer.",
                403,
            )
            return response(environ, start_response)
        if environ.get("REQUEST_METHOD") not in {"GET", "HEAD"}:
            response = _json_response("method not allowed", 405)
            response.headers["Allow"] = "GET, HEAD"
            return response(environ, start_response)

        index_path = self._dist_root / "index.html"
        if not index_path.is_file():
            response = _json_response(
                "Dashboard build unavailable. Build on a development machine or CI "
                "and copy dist/ into the project root.",
                503,
            )
            return response(environ, start_response)

        requested = environ.get("PATH_INFO", "/").lstrip("/") or "index.html"
        try:
            candidate = (self._dist_root / requested).resolve()
            candidate.relative_to(self._dist_root)
        except (OSError, RuntimeError, ValueError):
            response = _json_response("dashboard file not found", 404)
            return response(environ, start_response)

        if not candidate.is_file():
            if Path(requested).suffix:
                response = _json_response("dashboard file not found", 404)
                return response(environ, start_response)
            candidate = index_path

        try:
            content_length = candidate.stat().st_size
            dashboard_file = candidate.open("rb")
        except OSError:
            response = _json_response("dashboard file unavailable", 503)
            return response(environ, start_response)

        content_type = (
            mimetypes.guess_type(candidate.name)[0]
            or "application/octet-stream"
        )
        response = Response(
            wrap_file(environ, dashboard_file),
            content_type=content_type,
            direct_passthrough=True,
        )
        response.content_length = content_length
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if candidate.name == "sw.js":
            response.headers["Service-Worker-Allowed"] = "/"
        return response(environ, start_response)
