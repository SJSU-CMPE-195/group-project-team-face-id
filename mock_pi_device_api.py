"""Fake Pi API for developing the remote-camera UI without Raspberry Pi hardware.

Run from the repository root::

    python mock_pi_device_api.py

Then point the dashboard Device API Base URL at ``http://127.0.0.1:5055``.

The production routes are registered by :func:`pi_device_api.create_app`.
Only the ``/sim/*`` routes below are mock-only controls for deterministic
development and failure-injection scenarios.
"""

from __future__ import annotations

import importlib
import ipaddress
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import jsonify, request

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from car_face_auth.src.simulated_runtime import SimulatedPiRuntime  # noqa: E402
from pi_device_api import create_app  # noqa: E402


MOCK_DB_PATH = (REPO_ROOT / ".cache" / "mock_faceid.db").resolve()


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _json_object() -> dict[str, Any] | None:
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return body
    return None


def _loopback_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return value.rstrip(".").lower() == "localhost"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _loopback_url(value: str, *, allow_path: bool) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or not _loopback_address(parsed.hostname)
    ):
        return False
    if not allow_path and (
        parsed.path not in ("", "/") or parsed.query or parsed.fragment
    ):
        return False
    return port is None or 0 < port <= 65535


def _loopback_host() -> bool:
    host = request.headers.get("Host", "")
    try:
        parsed = urlsplit(f"http://{host}")
        server_port = int(request.environ.get("SERVER_PORT", ""))
        host_port = parsed.port if parsed.port is not None else 80
    except (TypeError, ValueError):
        return False
    return (
        bool(host)
        and parsed.hostname is not None
        and _loopback_address(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
        and host_port == server_port
    )


def _require_loopback_request():
    if not _loopback_address(request.remote_addr or ""):
        return _error("mock API is available only from loopback", 403)
    if not _loopback_host():
        return _error("mock API requires a loopback Host header", 403)
    origin = request.headers.get("Origin")
    if origin and not _loopback_url(origin, allow_path=False):
        return _error("mock API rejects non-loopback browser origins", 403)
    referer = request.headers.get("Referer")
    if referer and not _loopback_url(referer, allow_path=True):
        return _error("mock API rejects non-loopback browser origins", 403)
    return None


def _seed_demo_driver(db_module: Any) -> None:
    """Create the demo account only when the standalone server starts."""

    users = db_module.list_users_for_ui()
    if not any(user.get("name") == "Demo Driver" for user in users):
        db_module.add_user("Demo Driver")


def _register_simulation_routes(app, runtime: SimulatedPiRuntime):
    app.config["RUNTIME"] = runtime
    app.config["SIMULATED_RUNTIME"] = runtime

    @app.get("/sim/scenario")
    def sim_get_scenario():
        try:
            return jsonify(runtime.snapshot())
        except Exception as exc:  # developer-only endpoint: keep one envelope
            return _error(str(exc), 500)

    @app.route("/sim/scenario", methods=["PUT", "POST"])
    def sim_set_scenario():
        body = _json_object()
        if body is None:
            return _error("JSON object required", 400)
        try:
            return jsonify(runtime.configure(body))
        except Exception as exc:  # developer-only endpoint: keep one envelope
            return _error(str(exc), 400)

    @app.post("/sim/reset")
    def sim_reset():
        try:
            result = runtime.reset()
            return jsonify(result), 200 if result.get("ok") else 503
        except Exception as exc:  # developer-only endpoint: keep one envelope
            return _error(str(exc), 500)

    @app.get("/sim/commands")
    def sim_commands():
        try:
            return jsonify(runtime.commands)
        except Exception as exc:  # developer-only endpoint: keep one envelope
            return _error(str(exc), 500)

    return app


def create_mock_app(db_module: Any, runtime: SimulatedPiRuntime | None = None):
    """Build the canonical Device API with hardware-free runtime seams."""

    runtime_impl = runtime or SimulatedPiRuntime(db_module)
    app = create_app(db_module=db_module, runtime=runtime_impl)
    app.before_request(_require_loopback_request)
    return _register_simulation_routes(
        app,
        runtime_impl,
    )


def _load_standalone_database(database_path: Path = MOCK_DB_PATH):
    """Load db modules only after binding a fresh process to the mock path."""

    dedicated_path = database_path.resolve()
    loaded_db = sys.modules.get("db")
    loaded_api = sys.modules.get("db_api")
    if loaded_db is not None:
        loaded_path = Path(loaded_db.DB_PATH).resolve()
        if loaded_path != dedicated_path:
            raise RuntimeError(
                "refusing to repoint database modules already loaded for "
                f"{loaded_path}"
            )
        if loaded_api is None:
            loaded_api = importlib.import_module("db_api")
        return loaded_db, loaded_api
    if loaded_api is not None:
        raise RuntimeError("db_api was loaded without its database module")

    os.environ["FACEID_DB_PATH"] = str(dedicated_path)
    db_module = importlib.import_module("db")
    db_api_module = importlib.import_module("db_api")
    loaded_path = Path(db_module.DB_PATH).resolve()
    if loaded_path != dedicated_path:
        raise RuntimeError(
            f"mock database loaded from {loaded_path}, expected {dedicated_path}"
        )
    return db_module, db_api_module


if __name__ == "__main__":
    db_module, db_api_module = _load_standalone_database()
    db_module.init_db()
    _seed_demo_driver(db_api_module)
    app = create_mock_app(db_api_module)
    runtime = app.config["SIMULATED_RUNTIME"]
    startup = runtime.force_lock(reason="simulation_startup")
    if not startup.get("ok"):
        error = startup.get("error")
        print(f"WARNING: simulated startup lock failed: {error}", flush=True)
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="127.0.0.1", port=port, debug=False)
