"""Read-only Windows BLE health probe with pinned end-to-end TLS."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import ssl
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from wireless.ble_client import (  # noqa: E402
    BleApiClient,
    BleClientError,
    DiscoveredDevice,
)
from wireless.ble_http import BleHttpError, PinnedIdentity  # noqa: E402


class DiagnosticError(RuntimeError):
    pass


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        help="BLE address or platform device identifier; scans when omitted",
    )
    parser.add_argument("--device-id", required=True, help="expected host UUID from its QR")
    parser.add_argument(
        "--certificate-sha256",
        required=True,
        help="expected lowercase leaf DER SHA-256 from its QR",
    )
    parser.add_argument("--scan-timeout", type=float, default=8)
    return parser.parse_args()


async def _resolve_device(
    address: str | None,
    scan_timeout: float,
) -> DiscoveredDevice:
    devices = await BleApiClient.discover(scan_timeout)
    if address:
        matches = [
            device for device in devices if device.address.casefold() == address.casefold()
        ]
        if not matches:
            raise DiagnosticError(
                "The requested BASS address was not found in the current scan."
            )
        return matches[0]
    if not devices:
        raise DiagnosticError("No BASS BLE service was found.")
    if len(devices) != 1:
        choices = ", ".join(f"{item.name}={item.address}" for item in devices)
        raise DiagnosticError(f"Multiple BASS devices found; pass --address ({choices}).")
    return devices[0]


async def _run(arguments: argparse.Namespace) -> None:
    if arguments.scan_timeout <= 0:
        raise DiagnosticError("--scan-timeout must be positive")
    identity = PinnedIdentity(
        arguments.device_id,
        arguments.certificate_sha256,
    )
    device = await _resolve_device(arguments.address, arguments.scan_timeout)
    client = await BleApiClient.connect(device, identity)
    try:
        payload = await client.bootstrap()
        print(json.dumps(payload, indent=2, sort_keys=True))
    finally:
        await client.close()


def main() -> int:
    arguments = _arguments()
    try:
        asyncio.run(_run(arguments))
    except (
        BleClientError,
        BleHttpError,
        DiagnosticError,
        ValueError,
        asyncio.TimeoutError,
        OSError,
        ssl.SSLError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
