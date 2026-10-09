"""Strict parsing for PC-client invitation, activation, and recovery cards."""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
import uuid


CARD_VERSION = 3
MAX_CARD_CHARS = 4096
MAX_HTML_CHARS = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PURPOSE_SECRETS = {
    "pairing_invite": "invite_token",
    "activation": "activation_secret",
    "recovery": "recovery_secret",
}
IGNORED_DISCOVERY_FIELDS = frozenset({"address", "addresses", "port"})


class _CardScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._capturing = False
        self._parts: list[str] = []
        self.found = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        attributes = dict(attrs)
        if (
            attributes.get("id") == "bass-card"
            and (attributes.get("type") or "").casefold() == "application/json"
        ):
            self.found += 1
            self._capturing = True

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capturing:
            self._capturing = False

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    @property
    def card_text(self) -> str:
        return "".join(self._parts).strip()


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Card data contains duplicate JSON fields.")
        value[key] = item
    return value


def _from_text(value: str) -> object:
    if not value.strip():
        raise ValueError("Card data is empty.")
    if value.lstrip().startswith("<"):
        if len(value) > MAX_HTML_CHARS:
            raise ValueError("Card HTML is too large.")
        parser = _CardScriptParser()
        parser.feed(value)
        parser.close()
        if parser.found != 1 or not parser.card_text:
            raise ValueError(
                "This HTML has no embedded BASS card data; export a fresh card."
            )
        value = parser.card_text
    if len(value) > MAX_CARD_CHARS:
        raise ValueError("Card data is too large.")
    try:
        return json.loads(
            value,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError("Card data must be a JSON object.") from exc


def _reject_json_constant(value: str):
    raise ValueError(f"Invalid JSON number: {value}")


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Card device_id must be a UUID.")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("Card device_id must be a UUID.") from exc
    if str(parsed) != value:
        raise ValueError("Card device_id must be a canonical lowercase UUID.")
    return value


def _lower_hex(value: object, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Card {field} must be 64 lowercase hex digits.")
    return value


def parse_card(value: str | dict[str, object]) -> dict[str, object]:
    raw = _from_text(value) if isinstance(value, str) else value
    if not isinstance(raw, dict):
        raise ValueError("Card data must be a JSON object.")
    if type(raw.get("version")) is not int or raw["version"] != CARD_VERSION:
        raise ValueError(f"Card version must be {CARD_VERSION}.")
    purpose = raw.get("purpose")
    if not isinstance(purpose, str) or purpose not in PURPOSE_SECRETS:
        raise ValueError("Card purpose is unsupported.")

    secret_field = PURPOSE_SECRETS[purpose]
    allowed_fields = {
        "version",
        "device_id",
        "tls_certificate_sha256",
        "purpose",
        secret_field,
        *IGNORED_DISCOVERY_FIELDS,
    }
    unknown_fields = set(raw) - allowed_fields
    if unknown_fields:
        raise ValueError("Card contains unsupported fields.")
    secret_fields = set(PURPOSE_SECRETS.values())
    present_secrets = secret_fields.intersection(raw)
    if present_secrets != {secret_field}:
        raise ValueError("Card credential does not match its purpose.")

    normalized: dict[str, object] = {
        "version": CARD_VERSION,
        "device_id": _canonical_uuid(raw.get("device_id")),
        "tls_certificate_sha256": _lower_hex(
            raw.get("tls_certificate_sha256"),
            "tls_certificate_sha256",
            SHA256_PATTERN,
        ),
        "purpose": purpose,
        secret_field: _lower_hex(
            raw.get(secret_field),
            secret_field,
            SECRET_PATTERN,
        ),
    }
    return normalized
