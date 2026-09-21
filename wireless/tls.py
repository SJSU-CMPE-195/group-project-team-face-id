"""Persistent, QR-pinned TLS identity for the private-LAN listener."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import stat
import subprocess
import tempfile


@dataclass(frozen=True)
class TlsIdentity:
    path: Path
    certificate_sha256: str
    context: ssl.SSLContext


def _openssl() -> str:
    configured = os.environ.get("BASS_OPENSSL")
    executable = shutil.which(configured or "openssl")
    if executable:
        return executable
    if not configured and os.name == "nt":
        # Git for Windows already supplies OpenSSL outside the normal PATH.
        git = shutil.which("git")
        if git:
            bundled = Path(git).resolve().parent.parent / "usr/bin/openssl.exe"
            if bundled.is_file():
                return str(bundled)
    raise RuntimeError(
        "OpenSSL executable not found. Set BASS_OPENSSL to your existing "
        "openssl executable (Git for Windows includes one)."
    )


def _run_openssl(executable: str, *arguments: str) -> None:
    try:
        subprocess.run(
            [executable, *arguments],
            check=True,
            capture_output=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            "OpenSSL could not provision or validate the TLS certificate. "
            "Check the executable, file permissions, certificate expiry, and host clock."
        ) from exc


def _read_identity(path: Path, executable: str) -> TlsIdentity:
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise RuntimeError(f"TLS private-key permissions must be 600: {path}")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(str(path))
        pem = path.read_text(encoding="ascii")
        begin = pem.index("-----BEGIN CERTIFICATE-----")
        end = pem.index("-----END CERTIFICATE-----", begin)
        certificate = pem[begin : end + len("-----END CERTIFICATE-----")]
        der = ssl.PEM_cert_to_DER_cert(certificate)
        _run_openssl(executable, "x509", "-in", str(path), "-checkend", "0", "-noout")
    except (OSError, ValueError, UnicodeError, RuntimeError) as exc:
        raise RuntimeError(
            f"TLS identity is invalid or expired: {path}. Restore its backup, "
            "or explicitly replace it and re-pair phones; it was not regenerated."
        ) from exc
    return TlsIdentity(path, hashlib.sha256(der).hexdigest(), context)


def load_or_create_tls_identity(config_path: Path, device_id: str) -> TlsIdentity:
    """Publish key and certificate together, never replace an existing identity."""
    path = config_path.with_suffix(".tls.pem")
    executable = _openssl()
    if path.exists():
        return _read_identity(path, executable)

    with tempfile.TemporaryDirectory(prefix=".bass-tls-", dir=path.parent) as folder:
        private_dir = Path(folder)
        os.chmod(private_dir, 0o700)
        key_path = private_dir / "key.pem"
        cert_path = private_dir / "cert.pem"
        combined_path = private_dir / "identity.pem"
        host_name = f"bass-{device_id}.local"
        _run_openssl(
            executable,
            "req", "-x509", "-newkey", "rsa:3072", "-sha256",
            "-nodes", "-days", "3650",
            "-subj", f"/CN={host_name}",
            "-addext", f"subjectAltName=DNS:{host_name}",
            "-addext", "basicConstraints=critical,CA:FALSE",
            "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
            "-addext", "extendedKeyUsage=serverAuth",
            "-keyout", str(key_path),
            "-out", str(cert_path),
        )
        with combined_path.open("xb") as output:
            os.chmod(combined_path, 0o600)
            output.write(key_path.read_bytes())
            output.write(cert_path.read_bytes())
            output.flush()
            os.fsync(output.fileno())
        _read_identity(combined_path, executable)
        try:
            os.link(combined_path, path)
        except FileExistsError:
            # Another provisioning process won; use its complete identity.
            pass
    return _read_identity(path, executable)
