#!/usr/bin/env python3
"""Export a private owner activation QR, or back up and reset a stopped product."""

import argparse
import base64
from dataclasses import replace
from html import escape
import os
from pathlib import Path
import sqlite3
import sys


def export_activation(config, commissioning, output: Path) -> tuple[Path, Path]:
    from wireless.developer_reset import write_private_file
    from wireless.qr_export import render_qr_payload

    secret = commissioning.activation_material()
    if secret is None:
        raise RuntimeError(
            "This product is already claimed or legacy. Back up and fully reset "
            "it before issuing another owner activation QR."
        )
    payload = {
        **config.pairing_payload,
        "purpose": "activation",
        "activation_secret": secret,
    }
    image_bytes = render_qr_payload(payload)
    png_path = output / "bass-activation.png"
    html_path = output / "bass-activation.html"
    image_data = base64.b64encode(image_bytes).decode("ascii")
    html = f"""<!doctype html>
<html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BASS owner activation</title>
<style>body{{font-family:system-ui;text-align:center;margin:2rem;color:#140d2b}}
main{{max-width:34rem;margin:auto}}img{{width:min(90vw,28rem)}}
@media print{{button{{display:none}}}}</style>
<main><h1>{escape(config.name)}</h1><h2>Owner activation</h2>
<p>Scan in BASS Android, then set your name and six-digit PIN.</p>
<img src="data:image/png;base64,{image_data}" alt="One-time owner activation QR">
<p>The first successful activation becomes the super administrator.
This code cannot activate another owner afterward. Keep it private.</p>
<button onclick="window.print()">Print</button></main></html>
"""
    write_private_file(png_path, image_bytes)
    write_private_file(html_path, html.encode("utf-8"))
    return png_path, html_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "reset"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--request-id", help="required reset UUID; reuse for retries")
    parser.add_argument("--confirm", help="reset requires the exact value RESET")
    args = parser.parse_args()
    if args.action == "reset" and (not args.request_id or args.confirm != "RESET"):
        parser.error("reset requires --request-id UUID and --confirm RESET")
    database = args.db.expanduser().resolve(strict=True)
    config_path = args.config.expanduser().resolve(strict=True)
    output = args.output.expanduser().resolve()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["FACEID_DB_PATH"] = str(database)

    from wireless.auth_secret import load_auth_secret
    from wireless.commissioning import CommissioningStore
    from wireless.config import read_device_config
    from wireless.developer_reset import (
        DeveloperReset,
        _canonical_uuid,
        assert_reset_complete,
        create_product_backup,
    )
    from wireless.hardware_events import HardwareEvents
    from wireless.host_identity import bind_host_identity
    from wireless.host_lock import database_process_lock
    from wireless.security import SecurityStore
    from wireless.tls import load_or_create_tls_identity
    from db import get_conn, init_db

    with database_process_lock(database):
        if args.action == "export":
            assert_reset_complete(config_path, database)
        config = read_device_config(config_path)
        if not config_path.with_suffix(".tls.pem").is_file():
            raise RuntimeError("Restore the matching TLS identity before using this tool.")
        tls = load_or_create_tls_identity(config_path, config.device_id)
        config = replace(config, tls_certificate_sha256=tls.certificate_sha256)
        secret = load_auth_secret(config_path, credentials_exist=True)
        backup_sources = (
            config_path,
            config_path.with_suffix(".tls.pem"),
            config_path.with_suffix(".auth.key"),
        )
        if args.action == "reset":
            request_id = _canonical_uuid(args.request_id)
            original = sqlite3.connect(database)
            try:
                backup = create_product_backup(
                    original, config_path.parent, backup_sources, request_id
                )
            finally:
                original.close()
            print(f"Backup before migration: {backup}", flush=True)
        init_db()
        security = SecurityStore(get_conn, secret)
        security.ensure_schema()
        bind_host_identity(get_conn, tls.certificate_sha256, config.device_id)
        commissioning = CommissioningStore(security, config, None)
        commissioning.ensure_schema()
        if args.action == "reset":
            events = HardwareEvents(security, commissioning.status)
            reset = DeveloperReset(
                security=security,
                get_conn=get_conn,
                state_dir=config_path.parent,
                backup_sources=backup_sources,
                reset_product=commissioning.reset_product,
                generation_provider=commissioning.status,
                events=events,
                quiesce_callback=lambda: None,
                reload_callback=lambda: None,
            )
            print(f"Reset request: {args.request_id}", flush=True)
            receipt = reset.execute(args.request_id, args.confirm)
            if commissioning.status()["generation"] != receipt["generation"]:
                raise RuntimeError("This reset receipt belongs to an earlier product generation.")
            print(f"Backup: {receipt['backup_directory']}")
        security.validate_role_state()
        png_path, html_path = export_activation(config, commissioning, output)
        print(f"Owner activation QR: {png_path}")
        print(f"Printable QR: {html_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
