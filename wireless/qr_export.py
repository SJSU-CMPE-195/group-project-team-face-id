"""Create local-only printable pairing artifacts."""

from __future__ import annotations

import base64
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile

from .config import DeviceConfig


def pairing_payload_json(config: DeviceConfig) -> str:
    return json.dumps(config.pairing_payload, separators=(",", ":"), sort_keys=True)


def _write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def render_pairing_qr(config: DeviceConfig) -> bytes:
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_Q
    except ImportError as exc:
        raise RuntimeError(
            "QR export requires qrcode and Pillow; install requirements-wireless.txt"
        ) from exc

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_Q,
        box_size=10,
        border=4,
    )
    qr.add_data(pairing_payload_json(config))
    qr.make(fit=True)
    image = qr.make_image(fill_color="#140d2b", back_color="white")
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    return image_bytes.getvalue()


def export_pairing_qr(config: DeviceConfig, output_dir: Path) -> tuple[Path, Path]:
    image_bytes = render_pairing_qr(config)

    output_dir = output_dir.expanduser().resolve()
    png_path = output_dir / "bass-pairing.png"
    html_path = output_dir / "bass-pairing.html"
    _write_private(png_path, image_bytes)

    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BASS pairing QR</title>
  <style>
    body {{ font-family: system-ui, sans-serif; text-align: center; margin: 2rem;
            color: #140d2b; }}
    main {{ max-width: 34rem; margin: auto; }}
    img {{ width: min(92vw, 28rem); image-rendering: pixelated; }}
    code {{ overflow-wrap: anywhere; }}
    @media print {{ button {{ display: none; }} body {{ margin: 0; }} }}
  </style>
</head>
<body>
  <main>
    <h1>{escape(config.name)}</h1>
    <p>Scan this code in the BASS Android app.</p>
    <img src="data:image/png;base64,{encoded_image}" alt="BASS device pairing QR code">
    <p>Device ID: <code>{escape(config.device_id)}</code></p>
    <button type="button" onclick="window.print()">Print</button>
  </main>
</body>
</html>
"""
    _write_private(html_path, html.encode("utf-8"))
    return png_path, html_path
