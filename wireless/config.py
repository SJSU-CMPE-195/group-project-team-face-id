"""Persistent device identity used by QR pairing and API authentication."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import uuid


PROTOCOL_VERSION = 1
PAIRING_KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class DeviceConfigError(RuntimeError):
    """The persistent device configuration is missing or malformed."""


@dataclass(frozen=True)
class DeviceConfig:
    version: int
    device_id: str
    pairing_key: str
    name: str

    @property
    def pairing_payload(self) -> dict[str, object]:
        return {
            "version": self.version,
            "device_id": self.device_id,
            "pairing_key": self.pairing_key,
        }


def default_config_path() -> Path:
    configured_path = os.environ.get("BASS_DEVICE_CONFIG")
    if configured_path:
        return Path(configured_path).expanduser().resolve()

    configured_state_dir = os.environ.get("BASS_STATE_DIR")
    if configured_state_dir:
        state_dir = Path(configured_state_dir).expanduser()
    elif os.name == "nt":
        state_dir = Path.home() / ".bass"
    else:
        state_dir = Path(
            os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")
        ) / "bass"
    return (state_dir / "device.json").resolve()


def read_device_config(
    path: Path, *, require_private_permissions: bool = True
) -> DeviceConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeviceConfigError(f"device config does not exist: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeviceConfigError(f"device config is unreadable or malformed: {path}") from exc

    permissions_are_public = stat.S_IMODE(path.stat().st_mode) & 0o077
    if os.name != "nt" and require_private_permissions and permissions_are_public:
        raise DeviceConfigError(f"device config permissions must be 600: {path}")

    if not isinstance(raw, dict):
        raise DeviceConfigError(f"device config must be a JSON object: {path}")
    if raw.get("version") != PROTOCOL_VERSION:
        raise DeviceConfigError(
            f"device config version must be {PROTOCOL_VERSION}: {path}"
        )

    device_id = raw.get("device_id")
    try:
        parsed_device_id = uuid.UUID(device_id) if isinstance(device_id, str) else None
    except ValueError as exc:
        raise DeviceConfigError(f"device_id must be a UUID: {path}") from exc
    if parsed_device_id is None or str(parsed_device_id) != device_id:
        raise DeviceConfigError(f"device_id must be a canonical UUID: {path}")

    pairing_key = raw.get("pairing_key")
    if not isinstance(pairing_key, str) or not PAIRING_KEY_PATTERN.fullmatch(
        pairing_key
    ):
        raise DeviceConfigError(f"pairing_key must be 64 lowercase hex digits: {path}")

    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise DeviceConfigError(f"name must contain 1 to 80 characters: {path}")

    return DeviceConfig(
        version=PROTOCOL_VERSION,
        device_id=device_id,
        pairing_key=pairing_key,
        name=name.strip(),
    )


def load_or_create_device_config(path: Path | None = None) -> DeviceConfig:
    config_path = (path or default_config_path()).resolve()
    if config_path.exists():
        return read_device_config(config_path)

    parent_was_created = not config_path.parent.exists()
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and parent_was_created:
        os.chmod(config_path.parent, 0o700)

    device_id = str(uuid.uuid4())
    configured_name = os.environ.get("BASS_DEVICE_NAME", "").strip()
    device_name = configured_name or f"BASS-{device_id[-6:].upper()}"
    if len(device_name) > 80:
        raise DeviceConfigError("BASS_DEVICE_NAME must contain 1 to 80 characters")
    config = DeviceConfig(
        version=PROTOCOL_VERSION,
        device_id=device_id,
        pairing_key=secrets.token_hex(32),
        name=device_name,
    )
    encoded = (
        json.dumps(
            {
                **config.pairing_payload,
                "name": config.name,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", dir=config_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as config_file:
            config_file.write(encoded)
            config_file.flush()
            os.fsync(config_file.fileno())
        os.chmod(temporary_path, 0o600)
        try:
            # A same-directory hard link publishes the complete file in one
            # operation and fails rather than replacing a concurrent identity.
            os.link(temporary_path, config_path)
        except FileExistsError:
            return read_device_config(config_path)
        except OSError as exc:
            raise DeviceConfigError(
                f"device config could not be created atomically: {config_path}"
            ) from exc
        return config
    finally:
        temporary_path.unlink(missing_ok=True)
