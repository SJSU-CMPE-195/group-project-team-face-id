"""Reusable asynchronous BASS BLE client with pinned JSON HTTPS requests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import re
from typing import Any, Awaitable, Callable, TypeVar

from .ble_http import (
    CommittedResponse,
    JsonRequest,
    PinnedIdentity,
    PinnedIdentityError,
    PinnedJsonExchange,
    TOKEN_PATTERN,
)
from .ble_protocol import (
    CLOSED_FRAME,
    PROTOCOL_INFO,
    PROTOCOL_INFO_UUID,
    SERVICE_UUID,
    TUNNEL_UUID,
    ClientOpcode,
    ServerOpcode,
    tls_payload_capacity,
)


EXPECTED_API_VERSION = 4
GATT_CALLBACK_TIMEOUT_SECONDS = 10.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 60.0
CLOSE_TIMEOUT_SECONDS = 5.0
WAIT_DELAY_SECONDS = 0.1
MAX_PENDING_REQUESTS = 16
PIN_PATTERN = re.compile(r"^[0-9]{6}$")
ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
TARGET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{0,128}$")
REVOKED_CREDENTIAL_CODES = frozenset(
    {"invalid_device_credential", "stale_principal", "unauthorized"}
)
T = TypeVar("T")


class BleClientError(RuntimeError):
    """Base error for the Windows BLE client."""


class BleDependencyError(BleClientError):
    pass


class BleProtocolError(BleClientError):
    pass


class BleRemoteError(BleClientError):
    def __init__(self, code: int) -> None:
        super().__init__(f"BLE gateway error code 0x{code:02x}")
        self.code = code


class BleTransportUnusableError(BleClientError):
    pass


class BleRequestQueueFullError(BleClientError):
    pass


class BleHttpStatusError(BleClientError):
    def __init__(self, response: CommittedResponse, operation: str) -> None:
        super().__init__(f"{operation} returned HTTP {response.status}")
        self.response = response


@dataclass(frozen=True)
class DiscoveredDevice:
    """Browser-safe discovery summary retaining its private Bleak device handle."""

    key: str
    name: str
    address: str
    _device: object = field(repr=False, compare=False)

    def summary(self) -> dict[str, str]:
        return {"key": self.key, "name": self.name, "address": self.address}


class BleTunnel:
    def __init__(self, client: Any) -> None:
        self._client = client
        mtu = max(23, int(getattr(client, "mtu_size", 23)))
        self._payload_capacity = tls_payload_capacity(mtu)

    async def open(self) -> None:
        await self._write(bytes((ClientOpcode.OPEN, PROTOCOL_INFO[0])))
        frame = await self._read_non_wait(GATT_CALLBACK_TIMEOUT_SECONDS)
        if frame != bytes((ServerOpcode.OPEN_OK, PROTOCOL_INFO[0])):
            self._raise_unexpected(frame, "OPEN_OK")

    async def write_tls(self, payload: bytes) -> None:
        for offset in range(0, len(payload), self._payload_capacity):
            chunk = payload[offset : offset + self._payload_capacity]
            await self._write(bytes((ClientOpcode.DATA,)) + chunk)

    async def read_tls(self, timeout: float) -> bytes:
        frame = await self._read_non_wait(timeout)
        if frame and frame[0] == ServerOpcode.DATA and len(frame) > 1:
            return frame[1:]
        self._raise_unexpected(frame, "DATA")

    async def close(self) -> None:
        await asyncio.wait_for(self._close(), timeout=CLOSE_TIMEOUT_SECONDS)

    async def _close(self) -> None:
        await self._write(bytes((ClientOpcode.CLOSE,)))
        deadline = asyncio.get_running_loop().time() + CLOSE_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise BleTransportUnusableError(
                    "BLE CLOSE did not reach CLOSED within 5 seconds"
                )
            frame = await self._read_non_wait(remaining)
            if frame == CLOSED_FRAME:
                return
            if frame and frame[0] == ServerOpcode.DATA and len(frame) > 1:
                continue
            self._raise_unexpected(frame, "DATA or CLOSED")

    async def _write(self, value: bytes) -> None:
        await asyncio.wait_for(
            self._client.write_gatt_char(TUNNEL_UUID, value, response=True),
            timeout=GATT_CALLBACK_TIMEOUT_SECONDS,
        )

    async def _read_non_wait(self, timeout: float) -> bytes:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("BLE read timed out")
            frame = bytes(
                await asyncio.wait_for(
                    self._client.read_gatt_char(TUNNEL_UUID),
                    timeout=min(GATT_CALLBACK_TIMEOUT_SECONDS, remaining),
                )
            )
            if frame == bytes((ServerOpcode.WAIT,)):
                await asyncio.sleep(min(WAIT_DELAY_SECONDS, remaining))
                continue
            if len(frame) == 2 and frame[0] == ServerOpcode.ERROR:
                raise BleRemoteError(frame[1])
            return frame

    @staticmethod
    def _raise_unexpected(frame: bytes, expected: str) -> None:
        encoded = frame.hex() if frame else "empty"
        raise BleProtocolError(f"Expected {expected}; received {encoded}")


class BleApiClient:
    def __init__(
        self,
        client: Any,
        identity: PinnedIdentity,
        *,
        request_timeout: float,
    ) -> None:
        self._client = client
        self._identity = identity
        self._request_timeout = request_timeout
        self._request_lock = asyncio.Lock()
        self._pending_requests = 0
        self._bearer: str | None = None
        self._authenticated_device_id: str | None = None
        self._authenticated_user_id: str | None = None
        self._bootstrapped = False
        self._closed = False
        self._reusable = True

    @staticmethod
    async def discover(scan_timeout: float = 8.0) -> list[DiscoveredDevice]:
        if scan_timeout <= 0:
            raise ValueError("scan_timeout must be positive")
        _client_type, scanner_type = _load_bleak()
        discovered = await scanner_type.discover(
            timeout=scan_timeout,
            return_adv=True,
            service_uuids=[SERVICE_UUID],
        )
        matches: list[DiscoveredDevice] = []
        for device, advertisement in discovered.values():
            advertised_services = {
                item.lower() for item in advertisement.service_uuids or ()
            }
            if SERVICE_UUID.lower() not in advertised_services:
                continue
            address = str(device.address)
            matches.append(
                DiscoveredDevice(
                    key=address,
                    name=str(device.name or advertisement.local_name or "BASS"),
                    address=address,
                    _device=device,
                )
            )
        return sorted(matches, key=lambda item: (item.name.casefold(), item.address))

    @classmethod
    async def connect(
        cls,
        device: DiscoveredDevice,
        identity: PinnedIdentity,
        *,
        connect_timeout: float = GATT_CALLBACK_TIMEOUT_SECONDS,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> "BleApiClient":
        if connect_timeout <= 0 or request_timeout <= 0:
            raise ValueError("timeouts must be positive")
        client_type, _scanner_type = _load_bleak()
        instance: BleApiClient | None = None

        def disconnected(_client: Any) -> None:
            if instance is not None:
                instance._reusable = False
                instance._clear_authentication()

        bleak_client = client_type(
            device._device,
            disconnected_callback=disconnected,
            services=[SERVICE_UUID],
            timeout=connect_timeout,
        )
        try:
            await asyncio.wait_for(bleak_client.connect(), timeout=connect_timeout)
            protocol_info = bytes(
                await asyncio.wait_for(
                    bleak_client.read_gatt_char(PROTOCOL_INFO_UUID),
                    timeout=GATT_CALLBACK_TIMEOUT_SECONDS,
                )
            )
            if protocol_info != PROTOCOL_INFO:
                raise BleProtocolError(
                    f"Unsupported BLE protocol information: {protocol_info.hex()}"
                )
        except BaseException:
            await _best_effort_disconnect(bleak_client)
            raise

        instance = cls(
            bleak_client,
            identity,
            request_timeout=request_timeout,
        )
        return instance

    @property
    def is_reusable(self) -> bool:
        return (
            not self._closed
            and self._reusable
            and bool(getattr(self._client, "is_connected", False))
        )

    async def bootstrap(self) -> dict[str, Any]:
        async def operation() -> dict[str, Any]:
            response = await self._exchange_unlocked(JsonRequest("GET", "/health"))
            if response.status != 200:
                raise BleHttpStatusError(response, "BLE identity bootstrap")
            payload = _json_object(response, "BLE identity bootstrap")
            if payload.get("device_id") != self._identity.device_id:
                raise PinnedIdentityError(
                    "Pinned TLS peer returned a different host UUID"
                )
            if payload.get("protocol_version") != EXPECTED_API_VERSION:
                raise PinnedIdentityError(
                    f"Expected API v{EXPECTED_API_VERSION}; received "
                    f"v{payload.get('protocol_version')}"
                )
            if payload.get("ok") is not True or payload.get("service") != "bass-wireless":
                raise PinnedIdentityError(
                    "Health response does not identify a ready BASS service"
                )
            if not response.transport_reusable:
                raise BleTransportUnusableError(
                    response.teardown_error or "BLE bootstrap teardown failed"
                )
            self._bootstrapped = True
            return payload

        try:
            return await self._submit(operation)
        except PinnedIdentityError:
            self._clear_authentication()
            await self._poison()
            raise

    async def authenticate(self, bearer: str) -> dict[str, Any]:
        if not TOKEN_PATTERN.fullmatch(bearer):
            raise ValueError("invalid bearer token")

        async def operation() -> dict[str, Any]:
            self._require_bootstrapped()
            self._clear_authentication()
            response = await self._exchange_unlocked(
                JsonRequest("GET", "/api/me"),
                bearer=bearer,
            )
            if response.status == 401:
                self._clear_authentication()
            if response.status != 200:
                raise BleHttpStatusError(response, "BLE authentication")
            payload = _json_object(response, "BLE authentication")
            user = payload.get("user")
            device_id = payload.get("device_id")
            if not isinstance(user, dict) or not isinstance(device_id, str):
                raise PinnedIdentityError("Authenticated identity response is malformed")
            user_id = user.get("id")
            if not isinstance(user_id, str) or not user_id:
                raise PinnedIdentityError("Authenticated identity response is malformed")
            if not response.transport_reusable:
                raise BleTransportUnusableError(
                    response.teardown_error or "BLE authentication teardown failed"
                )
            self._bearer = bearer
            self._authenticated_device_id = device_id
            self._authenticated_user_id = user_id
            return payload

        try:
            return await self._submit(operation)
        except PinnedIdentityError:
            self._clear_authentication()
            await self._poison()
            raise

    async def request_json(
        self,
        request: JsonRequest,
        *,
        bearer: bool = True,
    ) -> CommittedResponse:
        async def operation() -> CommittedResponse:
            self._require_bootstrapped()
            authorization = self._require_bearer() if bearer else None
            response = await self._exchange_unlocked(request, bearer=authorization)
            _require_json_response(response)
            self._drop_bearer_on_unauthorized(response)
            return response

        try:
            return await self._submit(operation)
        except PinnedIdentityError:
            await self._poison()
            raise

    async def session_login(self, pin: str) -> CommittedResponse:
        _validate_pin(pin)

        async def operation() -> CommittedResponse:
            self._require_bootstrapped()
            response = await self._exchange_unlocked(
                JsonRequest("POST", "/api/session-login", {"pin": pin}),
                bearer=self._require_bearer(),
            )
            _require_json_response(response)
            self._drop_bearer_on_unauthorized(response)
            if response.status == 200:
                self._validate_session_identity(response)
            return response

        try:
            return await self._submit(operation)
        except PinnedIdentityError:
            await self._poison()
            raise

    async def grant_then_request(
        self,
        *,
        pin: str,
        action: str,
        target_user_id: str,
        request: JsonRequest,
    ) -> CommittedResponse:
        _validate_pin(pin)
        if not ACTION_PATTERN.fullmatch(action):
            raise ValueError("invalid operation action")
        if not TARGET_PATTERN.fullmatch(target_user_id):
            raise ValueError("invalid operation target")

        async def operation() -> CommittedResponse:
            self._require_bootstrapped()
            bearer = self._require_bearer()
            grant_response = await self._exchange_unlocked(
                JsonRequest(
                    "POST",
                    "/api/operation-grants",
                    {
                        "pin": pin,
                        "action": action,
                        "target_user_id": target_user_id,
                    },
                ),
                bearer=bearer,
            )
            _require_json_response(grant_response)
            self._drop_bearer_on_unauthorized(grant_response)
            if grant_response.status < 200 or grant_response.status >= 300:
                return grant_response
            if not grant_response.transport_reusable:
                raise BleTransportUnusableError(
                    grant_response.teardown_error
                    or "operation grant teardown failed before mutation"
                )
            grant_payload = _json_object(grant_response, "operation grant")
            grant_token = grant_payload.get("grant_token")
            expires_in = grant_payload.get("expires_in")
            if not isinstance(grant_token, str) or not TOKEN_PATTERN.fullmatch(grant_token):
                raise BleProtocolError("Pi returned an invalid operation grant")
            if not isinstance(expires_in, int) or isinstance(expires_in, bool):
                raise BleProtocolError("Pi returned an invalid operation grant lifetime")
            if expires_in not in range(1, 31):
                raise BleProtocolError("Pi returned an invalid operation grant lifetime")

            response = await self._exchange_unlocked(
                request,
                bearer=bearer,
                operation_grant=grant_token,
            )
            _require_json_response(response)
            self._drop_bearer_on_unauthorized(response)
            return response

        return await self._submit(operation)

    async def close(self) -> None:
        self._closed = True
        self._clear_authentication()
        async with self._request_lock:
            self._reusable = False
            await _best_effort_disconnect(self._client)

    async def _submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        if self._pending_requests >= MAX_PENDING_REQUESTS:
            raise BleRequestQueueFullError("BLE request queue is full")
        self._pending_requests += 1
        try:
            return await asyncio.wait_for(
                self._run_serialized(operation),
                timeout=self._request_timeout,
            )
        finally:
            self._pending_requests -= 1

    async def _run_serialized(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._request_lock:
            self._ensure_reusable()
            return await operation()

    async def _exchange_unlocked(
        self,
        request: JsonRequest,
        *,
        bearer: str | None = None,
        operation_grant: str | None = None,
    ) -> CommittedResponse:
        self._ensure_reusable()
        tunnel = BleTunnel(self._client)
        exchange: PinnedJsonExchange | None = None
        committed: CommittedResponse | None = None
        try:
            await tunnel.open()
            exchange = PinnedJsonExchange(
                tunnel,
                self._identity,
                io_timeout=self._request_timeout,
            )
            committed = await exchange.request(
                request,
                bearer=bearer,
                operation_grant=operation_grant,
            )
        except BaseException as error:
            if isinstance(error, PinnedIdentityError):
                self._bearer = None
            await self._poison()
            raise

        teardown_errors: list[BaseException] = []
        try:
            await exchange.finish_tls()
        except (Exception, asyncio.CancelledError) as error:
            teardown_errors.append(error)
        try:
            await tunnel.close()
        except (Exception, asyncio.CancelledError) as error:
            teardown_errors.append(error)
        if teardown_errors:
            await self._poison()
            detail = "; ".join(str(error) or type(error).__name__ for error in teardown_errors)
            return committed.with_teardown_failure(BleTransportUnusableError(detail))
        return committed

    def _ensure_reusable(self) -> None:
        if not self.is_reusable:
            raise BleTransportUnusableError("BLE transport is not reusable")

    def _require_bootstrapped(self) -> None:
        if not self._bootstrapped:
            raise BleClientError("BLE identity bootstrap has not completed")

    def _require_bearer(self) -> str:
        if self._bearer is None:
            raise BleClientError("BLE API is not authenticated")
        return self._bearer

    def _drop_bearer_on_unauthorized(self, response: CommittedResponse) -> None:
        if response.status != 401:
            return
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code") in REVOKED_CREDENTIAL_CODES:
            self._clear_authentication()

    def _validate_session_identity(self, response: CommittedResponse) -> None:
        payload = _json_object(response, "session login")
        user = payload.get("user")
        if (
            not isinstance(user, dict)
            or payload.get("device_id") != self._authenticated_device_id
            or user.get("id") != self._authenticated_user_id
        ):
            self._clear_authentication()
            self._reusable = False
            raise PinnedIdentityError("Session identity changed during PIN confirmation")

    def _clear_authentication(self) -> None:
        self._bearer = None
        self._authenticated_device_id = None
        self._authenticated_user_id = None

    async def _poison(self) -> None:
        self._reusable = False
        self._clear_authentication()
        await _best_effort_disconnect(self._client)


def _load_bleak() -> tuple[type[Any], type[Any]]:
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise BleDependencyError(
            "Bleak is not installed. Install the optional Windows BLE client "
            "requirements before using BLE discovery."
        ) from exc
    return BleakClient, BleakScanner


async def _best_effort_disconnect(client: Any) -> None:
    try:
        if getattr(client, "is_connected", False):
            await asyncio.wait_for(client.disconnect(), timeout=CLOSE_TIMEOUT_SECONDS)
    except (Exception, asyncio.CancelledError):
        pass


def _require_json_response(response: CommittedResponse) -> None:
    response.json()


def _json_object(response: CommittedResponse, operation: str) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise BleProtocolError(f"{operation} response must be a JSON object")
    return payload


def _validate_pin(pin: str) -> None:
    if not PIN_PATTERN.fullmatch(pin):
        raise ValueError("PIN must contain exactly six ASCII digits")
