"""Fake Pi API for developing the remote-camera UI without Raspberry Pi hardware.

Run from the repository root::

    python mock_pi_device_api.py

Then point the dashboard Device API Base URL at ``http://127.0.0.1:5055``.

The production routes are registered by :func:`pi_device_api.create_app`.
Only the ``/sim/*`` routes below are mock-only controls for deterministic
development and failure-injection scenarios.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from flask import jsonify, request

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Set this before importing db: db.DB_PATH is intentionally read at import
# time, and the standalone mock must never use the Pi's production database.
os.environ.setdefault("FACEID_DB_PATH", str(REPO_ROOT / ".cache" / "mock_faceid.db"))

from db import init_db  # noqa: E402
import db_api  # noqa: E402
from car_face_auth.src.simulated_runtime import SimulatedPiRuntime  # noqa: E402
from pi_device_api import create_app  # noqa: E402


def _error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def _json_object() -> dict[str, Any] | None:
    body = request.get_json(silent=True)
    if isinstance(body, dict):
        return body
    return None


def _seed_demo_driver() -> None:
    """Create the demo account only when the standalone server starts."""

    if not any(user.get("name") == "Demo Driver" for user in db_api.list_users_for_ui()):
        db_api.add_user("Demo Driver")


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


def create_mock_app(db_module: Any = db_api, runtime: SimulatedPiRuntime | None = None,
                    api_token: str | None = None):
    """Build the canonical Device API with hardware-free runtime seams."""

    runtime_impl = runtime or SimulatedPiRuntime(db_module)
    return _register_simulation_routes(
        create_app(db_module=db_module, runtime=runtime_impl, api_token=api_token),
        runtime_impl,
    )


app = create_mock_app()
runtime = app.config["SIMULATED_RUNTIME"]


if __name__ == "__main__":
    init_db()
    _seed_demo_driver()
    startup = runtime.force_lock(reason="simulation_startup")
    if not startup.get("ok"):
        print(f"WARNING: simulated startup lock failed: {startup.get('error')}", flush=True)
    port = int(os.environ.get("PORT", "5055"))
    app.run(host="0.0.0.0", port=port, debug=False)
