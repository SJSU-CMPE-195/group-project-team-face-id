"""Pinned-HTTPS commissioning routes with server-owned lifecycle decisions."""

from flask import g, jsonify, request

from pi_device_api import json_object
from .security import SecurityError


PUBLIC_COMMISSIONING_PATHS = frozenset({
    "/api/commissioning/status",
    "/api/commissioning/claim",
    "/api/commissioning/recover",
    "/api/commissioning/transfer/accept",
})


def require_https_commissioning() -> None:
    # The listener supplies this value. Forwarded headers are never trusted.
    if request.environ.get("wsgi.url_scheme") != "https":
        raise SecurityError(
            "https_required", 403, "Use the phone's pinned HTTPS connection.",
        )


def register_commissioning_routes(
    app, commissioning, events, transfer_handler, recovery_handler,
):
    @app.get("/api/commissioning/status")
    def commissioning_status():
        snapshot = events.snapshot()
        ownership = commissioning.status()
        return jsonify({
            "ownership": ownership["ownership"],
            "powered": snapshot["powered"],
            "maintenance": snapshot["maintenance"],
            "pairing_remaining_seconds": snapshot["pairing_remaining_seconds"],
            "recovery_remaining_seconds": snapshot["recovery_remaining_seconds"],
        })

    @app.post("/api/commissioning/claim")
    def claim_owner():
        return jsonify(commissioning.claim(json_object())), 201

    @app.post("/api/commissioning/recover")
    def recover_owner():
        return jsonify(recovery_handler(json_object()))

    @app.post("/api/commissioning/transfer/accept")
    def accept_owner_transfer():
        return jsonify(transfer_handler(json_object())), 201

    @app.post("/api/ownership/transfer")
    def start_owner_transfer():
        return jsonify(commissioning.start_transfer(g.principal, json_object())), 201
