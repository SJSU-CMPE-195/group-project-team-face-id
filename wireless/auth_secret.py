"""Persist the private PIN pepper separately from printable pairing material."""

from __future__ import annotations

import csv
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile


def load_auth_secret(config_path: Path, *, credentials_exist: bool) -> bytes:
    path = config_path.with_suffix(".auth.key")
    if path.is_symlink():
        raise RuntimeError("The PIN secret must not be a symlink.")
    if not path.exists():
        if credentials_exist:
            raise RuntimeError(
                "PIN secret missing. Restore device.auth.key with its database backup; "
                "it was not regenerated."
            )
        descriptor, name = tempfile.mkstemp(prefix=".bass-auth-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                if os.name == "nt":
                    identity = subprocess.run(
                        ["whoami", "/user", "/fo", "csv", "/nh"],
                        check=True, capture_output=True, text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    sid = next(csv.reader([identity.stdout.strip()]))[1]
                    subprocess.run(
                        ["icacls", str(temporary), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
                        check=True, capture_output=True,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    os.fchmod(output.fileno(), 0o600)
                output.write(secrets.token_bytes(32))
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"PIN secret permissions must be 600: {path}")
    secret = path.read_bytes()
    if len(secret) != 32:
        raise RuntimeError(f"PIN secret is invalid; restore its backup: {path}")
    return secret
