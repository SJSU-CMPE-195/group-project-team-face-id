"""Pinned JSON-over-HTTPS exchange helpers for the BASS BLE tunnel."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import ssl
from typing import Any, Protocol
from urllib.parse import urlsplit
import uuid


HTTP_HEADER_LIMIT = 16 * 1024
HTTP_BODY_LIMIT = 9 * 1024 * 1024
TLS_EOF_TIMEOUT_SECONDS = 2.0
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,512}$")
PIN_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class BleHttpError(RuntimeError):
    """Base error for the pinned HTTP layer."""


class PinnedIdentityError(BleHttpError):
    """The TLS peer does not match the configured BASS identity."""


class HttpProtocolError(BleHttpError):
    """The tunneled HTTP response violates the fixed-length JSON contract."""


class TlsTunnel(Protocol):
    async def write_tls(self, payload: bytes) -> None: ...

    async def read_tls(self, timeout: float) -> bytes: ...


@dataclass(frozen=True)
class PinnedIdentity:
    device_id: str
    certificate_sha256: str

    def __post_init__(self) -> None:
        try:
            parsed = uuid.UUID(self.device_id)
        except ValueError as exc:
            raise ValueError("device_id must be a UUID") from exc
        if str(parsed) != self.device_id:
            raise ValueError("device_id must be a canonical lowercase UUID")
        if not PIN_PATTERN.fullmatch(self.certificate_sha256):
            raise ValueError("certificate_sha256 must be 64 lowercase hex digits")

    @property
    def host_name(self) -> str:
        return f"bass-{self.device_id}.local"


@dataclass(frozen=True)
class JsonRequest:
    method: str
    path: str
    body: Any | None = None

    def __post_init__(self) -> None:
        method = self.method.upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("unsupported HTTP method")
        object.__setattr__(self, "method", method)
        _validate_origin_form_path(self.path)
        if method in {"GET", "DELETE"} and self.body is not None:
            raise ValueError(f"{method} requests cannot include a JSON body")


@dataclass(frozen=True)
class CommittedResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    transport_reusable: bool = True
    teardown_error: str | None = None

    def json(self) -> Any:
        content_type = self.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise HttpProtocolError("HTTP response is not application/json")
        try:
            return json.loads(
                self.body.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpProtocolError("HTTP response is not valid UTF-8 JSON") from exc

    def with_teardown_failure(self, error: BaseException) -> "CommittedResponse":
        return replace(
            self,
            transport_reusable=False,
            teardown_error=str(error) or type(error).__name__,
        )


class PinnedJsonExchange:
    """One fresh TLS connection carrying one fixed-length HTTP exchange."""

    def __init__(
        self,
        tunnel: TlsTunnel,
        identity: PinnedIdentity,
        *,
        io_timeout: float,
    ) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._incoming = ssl.MemoryBIO()
        self._outgoing = ssl.MemoryBIO()
        self._identity = identity
        self._tls = context.wrap_bio(
            self._incoming,
            self._outgoing,
            server_side=False,
            server_hostname=identity.host_name,
        )
        self._tunnel = tunnel
        self._io_timeout = io_timeout
        self._requested = False

    async def request(
        self,
        request: JsonRequest,
        *,
        bearer: str | None = None,
        operation_grant: str | None = None,
    ) -> CommittedResponse:
        if self._requested:
            raise RuntimeError("a TLS exchange carries exactly one HTTP request")
        self._requested = True
        _validate_optional_token(bearer, "bearer")
        _validate_optional_token(operation_grant, "operation grant")

        await self._handshake()
        self._validate_certificate()
        await self._write_plaintext(
            _encode_request(
                request,
                host_name=self._identity.host_name,
                bearer=bearer,
                operation_grant=operation_grant,
            )
        )
        return await self._read_http_response()

    async def finish_tls(self) -> None:
        await asyncio.wait_for(
            self._finish_tls(),
            timeout=TLS_EOF_TIMEOUT_SECONDS,
        )

    async def _finish_tls(self) -> None:
        deadline = _deadline(TLS_EOF_TIMEOUT_SECONDS)
        while True:
            try:
                self._tls.unwrap()
                await self._flush_ciphertext()
                return
            except ssl.SSLWantWriteError:
                await self._flush_ciphertext()
            except ssl.SSLWantReadError:
                await self._flush_ciphertext()
                self._incoming.write(
                    await self._tunnel.read_tls(_remaining(deadline, "TLS close"))
                )
            except ssl.SSLZeroReturnError:
                await self._flush_ciphertext()
                return

    async def _handshake(self) -> None:
        deadline = _deadline(self._io_timeout)
        while True:
            try:
                self._tls.do_handshake()
                await self._flush_ciphertext()
                return
            except ssl.SSLWantWriteError:
                await self._flush_ciphertext()
            except ssl.SSLWantReadError:
                await self._flush_ciphertext()
                self._incoming.write(
                    await self._tunnel.read_tls(_remaining(deadline, "TLS handshake"))
                )

    def _validate_certificate(self) -> None:
        der = self._tls.getpeercert(binary_form=True)
        if not der or not hmac.compare_digest(
            hashlib.sha256(der).hexdigest(),
            self._identity.certificate_sha256,
        ):
            raise PinnedIdentityError("TLS leaf certificate pin does not match")
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives.asymmetric import (
                dsa,
                ec,
                ed25519,
                ed448,
                padding,
                rsa,
            )
        except ImportError as exc:
            raise BleHttpError(
                "Install the reviewed cryptography dependency for pinned BLE HTTPS."
            ) from exc

        certificate = x509.load_der_x509_certificate(der)
        now = datetime.now(timezone.utc)
        if not certificate.not_valid_before_utc <= now <= certificate.not_valid_after_utc:
            raise PinnedIdentityError("TLS leaf certificate is not currently valid")
        if certificate.issuer != certificate.subject:
            raise PinnedIdentityError("TLS leaf certificate is not self-signed")
        try:
            constraints = certificate.extensions.get_extension_for_class(
                x509.BasicConstraints
            ).value
            names = certificate.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound as exc:
            raise PinnedIdentityError(
                "TLS leaf certificate lacks required constraints"
            ) from exc
        if constraints.ca:
            raise PinnedIdentityError("TLS leaf certificate must not be a CA")
        if self._identity.host_name not in names:
            raise PinnedIdentityError("TLS certificate host UUID name does not match")

        public_key = certificate.public_key()
        try:
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    certificate.signature_hash_algorithm,
                )
            elif isinstance(public_key, ec.EllipticCurvePublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    ec.ECDSA(certificate.signature_hash_algorithm),
                )
            elif isinstance(public_key, dsa.DSAPublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    certificate.signature_hash_algorithm,
                )
            elif isinstance(public_key, (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey)):
                public_key.verify(certificate.signature, certificate.tbs_certificate_bytes)
            else:
                raise PinnedIdentityError("Unsupported TLS certificate public key")
        except PinnedIdentityError:
            raise
        except Exception as exc:
            raise PinnedIdentityError("TLS leaf self-signature is invalid") from exc

    async def _write_plaintext(self, payload: bytes) -> None:
        view = memoryview(payload)
        deadline = _deadline(self._io_timeout)
        while view:
            try:
                written = self._tls.write(view)
                view = view[written:]
            except ssl.SSLWantWriteError:
                pass
            except ssl.SSLWantReadError:
                await self._flush_ciphertext()
                self._incoming.write(
                    await self._tunnel.read_tls(_remaining(deadline, "TLS write"))
                )
            await self._flush_ciphertext()

    async def _read_http_response(self) -> CommittedResponse:
        plaintext = bytearray()
        deadline = _deadline(self._io_timeout)
        while True:
            parsed = _parse_complete_http_response(plaintext)
            if parsed is not None:
                return parsed
            try:
                chunk = self._tls.read(16 * 1024)
                if not chunk:
                    raise HttpProtocolError(
                        "TLS ended before a complete HTTP response"
                    )
                plaintext.extend(chunk)
            except ssl.SSLWantWriteError:
                await self._flush_ciphertext()
            except ssl.SSLWantReadError:
                await self._flush_ciphertext()
                self._incoming.write(
                    await self._tunnel.read_tls(_remaining(deadline, "HTTP response"))
                )
            except ssl.SSLZeroReturnError as exc:
                raise HttpProtocolError(
                    "TLS ended before a complete HTTP response"
                ) from exc

    async def _flush_ciphertext(self) -> None:
        while True:
            payload = self._outgoing.read()
            if not payload:
                return
            await self._tunnel.write_tls(payload)


def _encode_request(
    request: JsonRequest,
    *,
    host_name: str,
    bearer: str | None,
    operation_grant: str | None,
) -> bytes:
    body = b""
    if request.body is not None:
        try:
            body = json.dumps(
                request.body,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("request body must be JSON serializable") from exc
    if len(body) > 64 * 1024:
        raise ValueError("JSON request body exceeds 64 KiB")

    headers = [
        f"{request.method} {request.path} HTTP/1.1",
        f"Host: {host_name}",
        "Accept: application/json",
        "Accept-Encoding: identity",
        "Cache-Control: no-store",
        "Connection: close",
        f"Content-Length: {len(body)}",
    ]
    if body:
        headers.append("Content-Type: application/json")
    if bearer:
        headers.append(f"Authorization: Bearer {bearer}")
    if operation_grant:
        headers.append(f"X-BASS-Operation-Grant: {operation_grant}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + body


def _parse_complete_http_response(raw: bytearray) -> CommittedResponse | None:
    marker = raw.find(b"\r\n\r\n")
    if marker < 0:
        if len(raw) > HTTP_HEADER_LIMIT:
            raise HttpProtocolError("HTTP response headers exceed 16 KiB")
        return None
    if marker > HTTP_HEADER_LIMIT:
        raise HttpProtocolError("HTTP response headers exceed 16 KiB")
    try:
        header_lines = bytes(raw[:marker]).decode("iso-8859-1").split("\r\n")
        version, status_text, _reason = header_lines[0].split(" ", 2)
        status = int(status_text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise HttpProtocolError("Malformed HTTP status line") from exc
    if version != "HTTP/1.1" or status not in range(100, 600):
        raise HttpProtocolError("Expected a valid HTTP/1.1 response")

    headers: dict[str, str] = {}
    for line in header_lines[1:]:
        if ":" not in line:
            raise HttpProtocolError("Malformed HTTP response header")
        name, value = line.split(":", 1)
        key = name.strip().lower()
        if not key or key in headers:
            raise HttpProtocolError("Duplicate or empty HTTP response header")
        headers[key] = value.strip()
    if "transfer-encoding" in headers:
        raise HttpProtocolError("Transfer-Encoding responses are unsupported")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise HttpProtocolError("Compressed HTTP responses are unsupported")

    value = headers.get("content-length")
    if value is None or not value.isdigit():
        raise HttpProtocolError("HTTP response requires a valid Content-Length")
    length = int(value)
    if length > HTTP_BODY_LIMIT:
        raise HttpProtocolError("HTTP response body exceeds 9 MiB")
    body = bytes(raw[marker + 4 :])
    if len(body) < length:
        return None
    if len(body) != length:
        raise HttpProtocolError("HTTP response contains bytes beyond Content-Length")
    return CommittedResponse(status, headers, body)


def _validate_origin_form_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("HTTP path must be absolute origin-form")
    if any(ord(character) <= 0x20 or ord(character) == 0x7F for character in path):
        raise ValueError("HTTP path contains control characters")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/"):
        raise ValueError("HTTP path must be absolute origin-form without a fragment")


def _validate_optional_token(value: str | None, name: str) -> None:
    if value is not None and not TOKEN_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {name} token")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise HttpProtocolError(f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _deadline(timeout: float) -> float:
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    return asyncio.get_running_loop().time() + timeout


def _remaining(deadline: float, operation: str) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError(f"{operation} timed out")
    return remaining
