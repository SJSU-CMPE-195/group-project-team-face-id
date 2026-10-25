"""Authenticated, discoverable BASS Device API for Windows PC or Raspberry Pi."""

from __future__ import annotations

import argparse
from dataclasses import replace
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import webbrowser

from wireless.config import (
    DeviceConfigError,
    default_config_path,
    load_or_create_device_config,
    read_device_config,
)
from wireless.qr_export import export_pairing_qr
from wireless.tls import load_or_create_tls_identity


REPO_ROOT = Path(__file__).resolve().parent
DASHBOARD_DIST_ROOT = REPO_ROOT / "dist"


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return parsed


def _parser() -> argparse.ArgumentParser:
    default_mode = os.environ.get("BASS_RUNTIME_MODE") or (
        "pc" if os.name == "nt" else "pi"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pc", "pi"), default=default_mode)
    parser.add_argument(
        "--hardware-simulator", action="store_true",
        help="PC only: local developer controls (already enabled by default)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("BASS_HOST"),
        help="loopback API bind address (must be 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=_port,
        default=_port(os.environ.get("BASS_PORT", "5056")),
        help="HTTPS API port (default: 5056)",
    )
    parser.add_argument(
        "--dashboard-port",
        type=_port,
        default=_port(os.environ.get("BASS_DASHBOARD_PORT", "5057")),
        help="loopback-only HTTP dashboard port (default: 5057)",
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--export-qr", type=Path, metavar="DIRECTORY")
    parser.add_argument("--show-qr", action="store_true")
    offline_commands = parser.add_mutually_exclusive_group()
    offline_commands.add_argument(
        "--migrate-security-only", action="store_true",
        help="apply the additive authorization schema, without opening listeners or hardware",
    )
    offline_commands.add_argument(
        "--rotate-tls-authorization-only", action="store_true",
        help="offline: bind a replacement TLS identity and revoke every device credential",
    )
    offline_commands.add_argument(
        "--provision-only",
        action="store_true",
        help="create or validate the persistent config, export QR if requested, then exit",
    )
    parser.add_argument("--validate-config", type=Path, metavar="PATH")
    parser.add_argument("--validate-transfer-config", type=Path, metavar="PATH")
    return parser


def _check_port(host: str, port: int) -> None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((host, port))
    except OSError as exc:
        raise RuntimeError(f"TCP port {port} is unavailable on {host}: {exc}") from exc


def _database_path(mode: str) -> Path:
    default_path = (
        REPO_ROOT / ".cache" / "mock_faceid.db"
        if mode == "pc"
        else Path("/home/pi/faceid/faceid.db")
    )
    return Path(os.environ.get("FACEID_DB_PATH", default_path)).expanduser().resolve()


def _prepare_database(mode: str) -> Path:
    database_path = _database_path(mode)
    os.environ["FACEID_DB_PATH"] = str(database_path)
    from wireless.developer_reset import protect_private_file, write_private_file

    if not database_path.exists():
        write_private_file(database_path, b"")
    protect_private_file(database_path)
    from db import init_db

    init_db()
    return database_path


def _persistent_identity_exists(database_path: Path) -> bool:
    """Detect state that makes silent config/TLS regeneration unsafe."""

    if not database_path.is_file():
        return False
    try:
        connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "host_identity" in tables and connection.execute(
                "SELECT 1 FROM host_identity WHERE id=1"
            ).fetchone():
                return True
            return "paired_devices" in tables and bool(
                connection.execute(
                    "SELECT 1 FROM paired_devices WHERE active=1 LIMIT 1"
                ).fetchone()
            )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"Cannot validate persistent host identity in {database_path}: {exc}"
        ) from exc


def _build_runtime(mode: str):
    import db_api

    if mode == "pc":
        from car_face_auth.src.pc_runtime import PcRuntime

        runtime = PcRuntime(db_api)
    else:
        from car_face_auth.src.pi_runtime import PiRuntime

        runtime = PiRuntime(db_api)
    return runtime


def _show_qr(config_path: Path, config, output_dir: Path | None) -> None:
    destination = output_dir or config_path.parent
    png_path, html_path = export_pairing_qr(config, destination)
    print(f"Pairing QR PNG:  {png_path}", flush=True)
    print(f"Printable QR:   {html_path}", flush=True)
    if not webbrowser.open(html_path.as_uri()):
        print("Open the printable QR path above in a local browser.", flush=True)


def _export_requested_qr(arguments, config_path: Path, config) -> None:
    if not arguments.export_qr and not arguments.show_qr:
        return
    destination = arguments.export_qr or config_path.parent
    if arguments.show_qr:
        _show_qr(config_path, config, destination)
    else:
        png_path, html_path = export_pairing_qr(config, destination)
        print(f"Pairing QR PNG:  {png_path}")
        print(f"Printable QR:   {html_path}")


def _print_host_ready(arguments, config, tls_identity, database_path: Path) -> None:
    print("BASS wireless host ready", flush=True)
    print(f"  Mode:          {arguments.mode}", flush=True)
    print(f"  Device:        {config.name} ({config.device_id})", flush=True)
    if arguments.mode == "pi":
        print(f"  API upstream:  https://127.0.0.1:{arguments.port} (BLE only)", flush=True)
    if (DASHBOARD_DIST_ROOT / "index.html").is_file():
        print(
            f"  Dashboard:     http://localhost:{arguments.dashboard_port}/",
            flush=True,
        )
    else:
        print(
            "  Dashboard:     unavailable; build on development/CI and copy dist/ "
            "into the project root",
            flush=True,
        )
    print(f"  TLS identity:  {tls_identity.path}", flush=True)
    print(f"  Database:      {database_path}", flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.validate_config or arguments.validate_transfer_config:
        return _run(arguments)
    from wireless.host_lock import database_process_lock

    with database_process_lock(_database_path(arguments.mode)):
        return _run(arguments)


def _run(arguments: argparse.Namespace) -> int:
    if arguments.host is None:
        arguments.host = "127.0.0.1"
    if arguments.host != "127.0.0.1":
        raise ValueError("BASS requires --host 127.0.0.1; LAN fallback is disabled.")
    if arguments.hardware_simulator and arguments.mode != "pc":
        raise ValueError("Hardware simulation requires explicit PC development mode.")
    offline_command = any((
        arguments.migrate_security_only,
        arguments.rotate_tls_authorization_only,
        arguments.provision_only,
        arguments.validate_config,
        arguments.validate_transfer_config,
    ))
    if arguments.hardware_simulator and offline_command:
        raise ValueError("Hardware simulation cannot be combined with offline commands.")
    arguments.hardware_simulator = arguments.mode == "pc" and not offline_command
    offline_security = arguments.migrate_security_only or arguments.rotate_tls_authorization_only
    if offline_security:
        database_argument = os.environ.get("FACEID_DB_PATH")
        if not database_argument or not Path(database_argument).expanduser().is_file():
            raise ValueError("Set FACEID_DB_PATH to an existing database for offline security changes.")
    if arguments.validate_config:
        config = read_device_config(arguments.validate_config.resolve())
        print(f"Valid BASS device config for {config.device_id}")
        return 0
    if arguments.validate_transfer_config:
        config = read_device_config(
            arguments.validate_transfer_config.resolve(),
            require_private_permissions=False,
        )
        print(f"Valid transferable BASS device config for {config.device_id}")
        return 0

    config_path = arguments.config.expanduser().resolve()
    from wireless.developer_reset import assert_reset_complete

    assert_reset_complete(config_path)
    identity_must_exist = (
        not arguments.rotate_tls_authorization_only
        and _persistent_identity_exists(_database_path(arguments.mode))
    )
    if identity_must_exist and (
        not config_path.is_file() or not config_path.with_suffix(".tls.pem").is_file()
    ):
        raise RuntimeError(
            "Persistent paired host state exists, but its device config or TLS identity "
            "is missing. Restore the matching backup; no replacement identity was created."
        )
    config = load_or_create_device_config(config_path)
    tls_identity = load_or_create_tls_identity(config_path, config.device_id)
    config = replace(config, tls_certificate_sha256=tls_identity.certificate_sha256)
    if arguments.provision_only:
        if arguments.export_qr or arguments.show_qr:
            destination = arguments.export_qr or config_path.parent
            if arguments.show_qr:
                _show_qr(config_path, config, destination)
            else:
                png_path, html_path = export_pairing_qr(config, destination)
                print(f"Pairing QR PNG:  {png_path}")
                print(f"Printable QR:   {html_path}")
        print(f"Provisioned BASS device {config.name} ({config.device_id})")
        return 0

    if arguments.port == arguments.dashboard_port:
        raise ValueError("HTTPS and local dashboard ports must be different.")
    if not offline_security:
        _check_port(arguments.host, arguments.port)
        _check_port("127.0.0.1", arguments.dashboard_port)
    database_path = _prepare_database(arguments.mode)
    from db import get_conn
    from wireless.auth_secret import load_auth_secret
    from wireless.security import SecurityStore

    with get_conn() as connection:
        has_security = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_security'"
        ).fetchone()
        credentials_exist = bool(has_security and connection.execute(
            "SELECT 1 FROM user_security LIMIT 1"
        ).fetchone())
    security = SecurityStore(get_conn, load_auth_secret(config_path, credentials_exist=credentials_exist))
    security.ensure_schema()
    from wireless.host_identity import bind_host_identity

    bind_host_identity(get_conn, tls_identity.certificate_sha256, config.device_id,
                       rotate=arguments.rotate_tls_authorization_only)
    if offline_security:
        from wireless.commissioning import CommissioningStore

        CommissioningStore(security, config, None).ensure_schema()
        security.validate_role_state()
        print(f"Authorization state prepared at {database_path}; no device was started.")
        if arguments.rotate_tls_authorization_only:
            print("All device credentials are revoked. Sign in locally and issue new pairing invitations.")
        return 0
    interrupted = security.enrollment.interrupt_pending()
    if interrupted:
        print(
            f"Marked {interrupted} unfinished enrollment request(s) interrupted.",
            flush=True,
        )
    runtime = _build_runtime(arguments.mode)
    from wireless.host_lifecycle import HostLifecycle

    lifecycle = HostLifecycle(security, config, runtime)
    if arguments.hardware_simulator:
        lifecycle.enable_developer_controls(config_path, arguments.dashboard_port)
    previous_signal_handlers: dict[signal.Signals, object] = {}
    active_ble_gateway = None
    shutdown_requested = False

    def request_shutdown(_signum, _frame):
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        if active_ble_gateway is not None:
            active_ble_gateway.request_shutdown()
            return
        raise KeyboardInterrupt

    try:
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            previous_signal_handlers[shutdown_signal] = signal.signal(
                shutdown_signal, request_shutdown
            )
        if not lifecycle.events.maintenance:
            if arguments.mode == "pc":
                runtime.initialize()
            startup_lock = runtime.force_lock(reason="wireless_startup")
            if not startup_lock.get("ok"):
                print(
                    f"WARNING: startup fail-safe reported: {startup_lock.get('error')}",
                    flush=True,
                )

        from pi_device_api import create_app
        import db_api
        from wireless.api import secure_wireless_app
        from wireless.server import serving_in_background, wireless_servers

        app = secure_wireless_app(
            create_app(db_module=db_api, runtime=runtime),
            config=config,
            mode=arguments.mode,
            port=arguments.dashboard_port,
            dist_root=DASHBOARD_DIST_ROOT,
            security=security,
            commissioning=lifecycle.commissioning,
            events=lifecycle.events,
            developer_control=lifecycle.developer_control,
            transfer_handler=None,
            recovery_handler=lifecycle.recover_owner,
        )
        if (
            arguments.mode == "pi"
            and os.environ.get("ESP32_TRIGGER_ENABLED", "1") != "0"
        ):
            runtime.start_esp32_link()
        with wireless_servers(
            app,
            host=arguments.host,
            port=arguments.port,
            dashboard_port=arguments.dashboard_port,
            tls_identity_path=tls_identity.path,
            ble_transport=arguments.mode == "pi",
        ) as https_server:
            try:
                if arguments.mode == "pi":
                    from wireless.ble_gateway import BluezBleGateway

                    with serving_in_background(
                        https_server,
                        host="127.0.0.1",
                        port=arguments.port,
                    ):
                        with BluezBleGateway(
                            device_id=config.device_id,
                            upstream_port=arguments.port,
                        ) as ble_gateway:
                            active_ble_gateway = ble_gateway

                            def ble_ready() -> None:
                                _export_requested_qr(arguments, config_path, config)
                                _print_host_ready(
                                    arguments,
                                    config,
                                    tls_identity,
                                    database_path,
                                )
                                print("  BLE clients:   up to 4", flush=True)

                            try:
                                ble_gateway.serve(on_ready=ble_ready)
                            finally:
                                print("Stopping BASS Bluetooth service...", flush=True)
                        print("Stopping BASS HTTP services...", flush=True)
                else:
                    _export_requested_qr(arguments, config_path, config)
                    _print_host_ready(arguments, config, tls_identity, database_path)
                    print(
                        f"  API:           https://127.0.0.1:{arguments.port}",
                        flush=True,
                    )
                    if arguments.hardware_simulator:
                        print(
                            "  Hardware:      use the Hardware tab in the dashboard",
                            flush=True,
                        )
                    https_server.serve()
            except KeyboardInterrupt:
                print("Stopping BASS wireless host...", flush=True)
    finally:
        try:
            print("Stopping BASS runtime...", flush=True)
            runtime.close()
        finally:
            for shutdown_signal, previous_handler in previous_signal_handlers.items():
                signal.signal(shutdown_signal, previous_handler)
    print("BASS wireless host stopped.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("BASS wireless host stopped.", file=sys.stderr)
        raise SystemExit(130)
    except (DeviceConfigError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
