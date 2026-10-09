"""Frozen BASS BLE v1 framing shared by the host and diagnostic client."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


SERVICE_UUID = "c74f82e0-a691-4f6f-a6b3-1220c3f7679a"
PROTOCOL_INFO_UUID = "c74f82e1-a691-4f6f-a6b3-1220c3f7679a"
TUNNEL_UUID = "c74f82e2-a691-4f6f-a6b3-1220c3f7679a"

PROTOCOL_VERSION = 1
PROTOCOL_INFO = bytes((PROTOCOL_VERSION,))
MAX_TLS_PAYLOAD = 511
MAX_DIRECTION_BUFFER = 32 * 1024


class ClientOpcode(IntEnum):
    OPEN = 0x01
    DATA = 0x02
    CLOSE = 0x03


class ServerOpcode(IntEnum):
    OPEN_OK = 0x81
    DATA = 0x82
    WAIT = 0x83
    CLOSED = 0x84
    ERROR = 0xFF


class ErrorCode(IntEnum):
    BUSY = 0x01
    BAD_STATE = 0x02
    UNSUPPORTED_VERSION = 0x03
    UPSTREAM_UNAVAILABLE = 0x04
    TIMEOUT = 0x05


@dataclass(frozen=True)
class ClientFrame:
    opcode: ClientOpcode
    payload: bytes = b""


class ProtocolViolation(ValueError):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


def tls_payload_capacity(mtu: int) -> int:
    if not isinstance(mtu, int) or isinstance(mtu, bool) or mtu < 5:
        raise ValueError("negotiated MTU must be at least 5")
    return min(mtu - 4, MAX_TLS_PAYLOAD)


def parse_client_frame(value: bytes, mtu: int) -> ClientFrame:
    if not value:
        raise ProtocolViolation(ErrorCode.BAD_STATE, "empty BLE frame")
    try:
        opcode = ClientOpcode(value[0])
    except ValueError as exc:
        raise ProtocolViolation(ErrorCode.BAD_STATE, "unknown client opcode") from exc

    if opcode is ClientOpcode.OPEN:
        if len(value) != 2:
            raise ProtocolViolation(ErrorCode.BAD_STATE, "OPEN must be two bytes")
        if value[1] != PROTOCOL_VERSION:
            raise ProtocolViolation(
                ErrorCode.UNSUPPORTED_VERSION,
                "unsupported BLE tunnel version",
            )
        return ClientFrame(opcode)

    if opcode is ClientOpcode.CLOSE:
        if len(value) != 1:
            raise ProtocolViolation(ErrorCode.BAD_STATE, "CLOSE must be one byte")
        return ClientFrame(opcode)

    payload = value[1:]
    if not payload:
        raise ProtocolViolation(ErrorCode.BAD_STATE, "DATA payload cannot be empty")
    if len(payload) > tls_payload_capacity(mtu):
        raise ProtocolViolation(ErrorCode.BAD_STATE, "DATA exceeds the negotiated MTU")
    return ClientFrame(opcode, payload)


def open_ok_frame() -> bytes:
    return bytes((ServerOpcode.OPEN_OK, PROTOCOL_VERSION))


def data_frame(payload: bytes, mtu: int) -> bytes:
    if not payload or len(payload) > tls_payload_capacity(mtu):
        raise ValueError("invalid server DATA payload")
    return bytes((ServerOpcode.DATA,)) + payload


def error_frame(code: ErrorCode) -> bytes:
    return bytes((ServerOpcode.ERROR, code))


WAIT_FRAME = bytes((ServerOpcode.WAIT,))
CLOSED_FRAME = bytes((ServerOpcode.CLOSED,))
