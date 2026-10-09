"""DPAPI-protected persistent state for the Windows BLE client."""

from __future__ import annotations

from collections.abc import Mapping
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile


MAX_STATE_BYTES = 64 * 1024
MAX_PROTECTED_BYTES = 128 * 1024


class _DataBlob(ctypes.Structure):
    _fields_ = (
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    )


def _default_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable; PC client state was not saved.")
    root = Path(local_app_data).expanduser()
    if not root.is_absolute():
        raise RuntimeError("LOCALAPPDATA must be an absolute path.")
    return root / "BASS" / "pc-client-state.dpapi"


def _dpapi(name: str, value: bytes | bytearray, *, clear_output: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("BASS PC client state requires Windows DPAPI.")
    if not value:
        raise ValueError("PC client state cannot be empty.")

    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    function = getattr(crypt32, name)
    function.argtypes = (
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p if name == "CryptProtectData" else ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    kernel32.LocalFree.restype = ctypes.c_void_p

    input_buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    input_blob = _DataBlob(
        len(value),
        ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output_blob = _DataBlob()
    try:
        if not function(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(output_blob),
        ):
            error = ctypes.get_last_error()
            raise OSError(error, f"Windows DPAPI failed: {ctypes.FormatError(error)}")
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        ctypes.memset(input_buffer, 0, len(input_buffer))
        if output_blob.pbData:
            if clear_output:
                ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _reject_persisted_pin(value: object) -> None:
    pending = [value]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            if "pin" in current:
                raise ValueError("PIN values must remain in memory and cannot be saved.")
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in visited:
                continue
            visited.add(identity)
            pending.extend(current)


def _reject_json_constant(value: str):
    raise ValueError(f"Invalid JSON number: {value}")


class PcClientStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._path = (
            Path(path).expanduser().resolve() if path is not None else _default_path()
        )

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, object] | None:
        try:
            protected_size = self._path.stat().st_size
        except FileNotFoundError:
            return None
        if not 0 < protected_size <= MAX_PROTECTED_BYTES:
            raise ValueError("Saved PC client state is invalid.")
        try:
            protected = self._path.read_bytes()
        except FileNotFoundError:
            return None
        if not protected or len(protected) > MAX_PROTECTED_BYTES:
            raise ValueError("Saved PC client state is invalid.")

        plaintext = bytearray(
            _dpapi("CryptUnprotectData", protected, clear_output=True)
        )
        try:
            if len(plaintext) > MAX_STATE_BYTES:
                raise ValueError("Saved PC client state is too large.")
            state = json.loads(
                plaintext.decode("utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Saved PC client state is invalid.") from exc
        finally:
            plaintext[:] = b"\x00" * len(plaintext)
        if not isinstance(state, dict):
            raise ValueError("Saved PC client state must be a JSON object.")
        _reject_persisted_pin(state)
        return state

    def save(self, state: Mapping[str, object]) -> None:
        if not isinstance(state, Mapping):
            raise TypeError("PC client state must be a mapping.")
        snapshot = dict(state)
        _reject_persisted_pin(snapshot)
        try:
            encoded = bytearray(
                json.dumps(
                    snapshot,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("PC client state must contain only JSON values.") from exc
        if len(encoded) > MAX_STATE_BYTES:
            encoded[:] = b"\x00" * len(encoded)
            raise ValueError("PC client state is too large.")

        try:
            protected = _dpapi("CryptProtectData", encoded, clear_output=False)
        finally:
            encoded[:] = b"\x00" * len(encoded)
        if len(protected) > MAX_PROTECTED_BYTES:
            raise ValueError("Protected PC client state is too large.")
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            dir=self._path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(protected)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self._path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
