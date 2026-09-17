"""Authenticated, discoverable BASS Device API for Windows PC or Raspberry Pi."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import socket
import sys
import webbrowser

from wireless.config import (
    DeviceConfigError,
    default_config_path,
    load_or_create_device_config,
    read_device_config,
)
from wireless.mdns import MdnsAdvertiser, MdnsError, discover_lan_addresses
from wireless.qr_export import export_pairing_qr


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
    parser.add_argument("--host", default=os.environ.get("BASS_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=_port,
        default=_port(os.environ.get("BASS_PORT", "5056")),
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--advertise-address",
        action="append",
        default=[],
        help="assigned private IPv4 address to advertise; may be repeated",
    )
    parser.add_argument("--export-qr", type=Path, metavar="DIRECTORY")
    parser.add_argument("--show-qr", action="store_true")
    parser.add_argument(
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


def _requested_addresses(arguments: argparse.Namespace) -> tuple[str, ...]:
    environment_addresses = os.environ.get("BASS_ADVERTISE_ADDRESS", "").split(",")
    return tuple(arguments.advertise_address or environment_addresses)


def _prepare_database(mode: str) -> Path:
    if "FACEID_DB_PATH" not in os.environ:
        default_path = (
            REPO_ROOT / ".cache" / "mock_faceid.db"
            if mode == "pc"
            else Path("/home/pi/faceid/faceid.db")
        )
        os.environ["FACEID_DB_PATH"] = str(default_path.resolve())
    database_path = Path(os.environ["FACEID_DB_PATH"]).expanduser().resolve()
    from db import init_db

    init_db()
    return database_path


def _build_runtime(mode: str):
    import db_api

    if mode == "pc":
        from car_face_auth.src.pc_runtime import PcRuntime

        runtime = PcRuntime(db_api)
        runtime.initialize()
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


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
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
    config = load_or_create_device_config(config_path)
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

    _check_port(arguments.host, arguments.port)
    database_path = _prepare_database(arguments.mode)
    runtime = _build_runtime(arguments.mode)
    advertiser = None
    previous_signal_handlers: dict[signal.Signals, object] = {}

    def request_shutdown(_signum, _frame):
        raise KeyboardInterrupt

    try:
        for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
            previous_signal_handlers[shutdown_signal] = signal.signal(
                shutdown_signal, request_shutdown
            )
        startup_lock = runtime.force_lock(reason="wireless_startup")
        if not startup_lock.get("ok"):
            print(
                f"WARNING: startup fail-safe reported: {startup_lock.get('error')}",
                flush=True,
            )

        from pi_device_api import create_app
        import db_api
        from wireless.api import secure_wireless_app

        app = secure_wireless_app(
            create_app(db_module=db_api, runtime=runtime),
            config=config,
            mode=arguments.mode,
            port=arguments.port,
            dist_root=DASHBOARD_DIST_ROOT,
        )
        requested_addresses = _requested_addresses(arguments)
        address_provider = lambda: discover_lan_addresses(requested_addresses)
        advertiser = MdnsAdvertiser(
            device_id=config.device_id,
            port=arguments.port,
            address_provider=address_provider,
            refresh_seconds=float(
                os.environ.get("BASS_MDNS_REFRESH_SECONDS", "10")
            ),
        )
        addresses = advertiser.start()
        if arguments.export_qr or arguments.show_qr:
            destination = arguments.export_qr or config_path.parent
            if arguments.show_qr:
                _show_qr(config_path, config, destination)
            else:
                png_path, html_path = export_pairing_qr(config, destination)
                print(f"Pairing QR PNG:  {png_path}")
                print(f"Printable QR:   {html_path}")
        print("BASS wireless host ready", flush=True)
        print(f"  Mode:          {arguments.mode}", flush=True)
        print(f"  Device:        {config.name} ({config.device_id})", flush=True)
        print(f"  API:           http://{addresses[0]}:{arguments.port}", flush=True)
        if (DASHBOARD_DIST_ROOT / "index.html").is_file():
            print(
                f"  Dashboard:     http://localhost:{arguments.port}/",
                flush=True,
            )
        else:
            print(
                "  Dashboard:     unavailable; build on development/CI and copy dist/ "
                "into the project root",
                flush=True,
            )
        print(f"  Database:      {database_path}", flush=True)
        print(f"  mDNS addresses: {', '.join(addresses)}", flush=True)
        try:
            app.run(
                host=arguments.host,
                port=arguments.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )
        except KeyboardInterrupt:
            print("Stopping BASS wireless host...", flush=True)
    finally:
        try:
            if advertiser is not None:
                advertiser.close()
        finally:
            try:
                runtime.close()
            finally:
                for shutdown_signal, previous_handler in previous_signal_handlers.items():
                    signal.signal(shutdown_signal, previous_handler)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("BASS wireless host stopped.", file=sys.stderr)
        raise SystemExit(130)
    except (DeviceConfigError, MdnsError, OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
