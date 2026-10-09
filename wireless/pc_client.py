"""Windows-side BASS controller for pinned HTTPS carried over BLE."""

from __future__ import annotations

import asyncio
import concurrent.futures
from contextlib import contextmanager
from copy import deepcopy
import ctypes
from functools import wraps
import importlib.util
import re
import secrets
import threading
import time
from typing import Any, Mapping, TypedDict
import uuid

from .ble_client import (
    BleApiClient,
    BleClientError,
    BleDependencyError,
    BleHttpStatusError,
    BleRequestQueueFullError,
    DiscoveredDevice,
)
from .ble_http import (
    CommittedResponse,
    JsonRequest,
    PinnedIdentity,
    PinnedIdentityError,
)
from .pc_client_cards import parse_card
from .pc_client_store import PcClientStore


PIN_PATTERN = re.compile(r"^[0-9]{6}$")
HEX_SECRET_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,512}$")
MAX_TEXT_LENGTH = 100
CONTROLLER_TIMEOUT_SECONDS = 75.0


class PcClientError(RuntimeError):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class CommissionOutcomeUnknown(PcClientError):
    def __init__(self) -> None:
        super().__init__(
            "commission_outcome_unknown",
            502,
            "The Pi may have completed this request. Reconnect and retry with "
            "the original PIN.",
        )


class OperationGrant(TypedDict):
    pin: str
    action: str
    target_user_id: str
    expires_at: float


def _serialized_setup(method):
    @wraps(method)
    def serialized(self, *args, **kwargs):
        with self._setup_lock:
            return method(self, *args, **kwargs)

    return serialized


class PcClientController:
    """Own one saved pairing and one persistent asyncio/WinRT thread."""

    def __init__(self, *, state_path=None) -> None:
        self._store = PcClientStore(state_path)
        self._state_lock = threading.RLock()
        self._setup_lock = threading.RLock()
        self._transport_lock = threading.Lock()
        self._closed = False
        self._client: BleApiClient | None = None
        self._connection_epoch = 0
        self._connection_error: dict[str, str] | None = None
        self._connected_name = ""
        self._staged_card: dict[str, object] | None = None
        self._candidates: dict[str, DiscoveredDevice] = {}
        self._state_error: str | None = None
        try:
            self._state = self._normalize_state(self._store.load())
        except (OSError, RuntimeError, TypeError, ValueError):
            self._state = self._empty_state()
            self._state_error = (
                "Saved PC client state could not be opened for this Windows user."
            )

        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="bass-pc-client-ble",
            daemon=True,
        )
        self._loop_thread.start()
        if not self._loop_ready.wait(timeout=5):
            raise RuntimeError("PC BLE event loop did not start.")

    @staticmethod
    def _empty_state() -> dict[str, object]:
        return {
            "version": 1,
            "generation": 0,
            "paired": None,
            "pending_commission": None,
            "recovery_card": None,
        }

    def _run_loop(self) -> None:
        initialized_com = False
        if hasattr(ctypes, "windll"):
            # Bleak's WinRT backend must live on the same MTA thread as this loop.
            result = ctypes.windll.ole32.CoInitializeEx(None, 0)
            initialized_com = result in {0, 1}
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()
            if initialized_com:
                ctypes.windll.ole32.CoUninitialize()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        with self._transport_lock:
            client = self._take_client()
            if client is not None:
                try:
                    self._submit(client.close(), allow_closed=True)
                except (PcClientError, RuntimeError):
                    pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._loop_thread.join(timeout=5)

    def status(self) -> dict[str, object]:
        with self._state_lock:
            paired = deepcopy(self._state["paired"])
            pending = deepcopy(self._state["pending_commission"])
            recovery_available = self._state["recovery_card"] is not None
            connected = self._client is not None and self._client.is_reusable
            if (
                self._client is not None
                and not connected
                and self._connection_error is None
            ):
                self._connection_error = self._lost_connection_error()
            dependency_error = self._dependency_error()
            if self._state_error:
                dependency_error = self._state_error
            phase = self._phase(
                paired=paired,
                pending=pending,
                connected=connected,
                dependency_error=dependency_error,
            )
            device = None
            user = None
            if isinstance(paired, dict):
                device = {
                    "device_id": paired["host_device_id"],
                    "name": paired["host_name"],
                }
                user = {
                    "id": paired["user_id"],
                    "name": paired["user_name"],
                }
            elif pending is not None:
                device = {
                    "device_id": pending["card"]["device_id"],
                    "name": pending.get("host_name", "BASS"),
                }
            elif self._staged_card is not None:
                device = {
                    "device_id": self._staged_card["device_id"],
                    "name": self._connected_name or "BASS",
                }
            pending_summary = None
            if isinstance(pending, dict):
                pending_summary = {
                    "purpose": pending["card"]["purpose"],
                    "device_id": pending["card"]["device_id"],
                    "retry_available": True,
                }
            card_source = pending["card"] if isinstance(pending, dict) else None
            if card_source is None:
                card_source = self._staged_card
            card_summary = None
            if isinstance(card_source, dict):
                card_summary = {
                    "purpose": card_source["purpose"],
                    "device_id": card_source["device_id"],
                }
            return {
                "ok": True,
                "phase": phase,
                "configured": paired is not None,
                "connected": connected,
                "connection_error": deepcopy(self._connection_error),
                "dependency": {
                    "ready": dependency_error is None,
                    "error": dependency_error,
                },
                "device": device,
                "user": user,
                "card": card_summary,
                "pending_commission": pending_summary,
                "recovery_download_available": recovery_available,
            }

    def security_status(self) -> dict[str, object]:
        with self._state_lock:
            paired = self._state["paired"]
            if not isinstance(paired, dict):
                return {
                    "configured": False,
                    "ownership": "unclaimed",
                    "device_id": "",
                    "generation": int(self._state["generation"]),
                }
            return {
                "configured": True,
                "ownership": "claimed",
                "device_id": paired["host_device_id"],
                "generation": int(self._state["generation"]),
            }

    def paired_identity(self) -> dict[str, str] | None:
        with self._state_lock:
            paired = self._state["paired"]
            if not isinstance(paired, dict):
                return None
            return {
                "user_id": str(paired["user_id"]),
                "user_name": str(paired["user_name"]),
            }

    def credential_snapshot(self) -> dict[str, object] | None:
        with self._state_lock:
            paired = self._state["paired"]
            if not isinstance(paired, dict):
                return None
            return {
                "generation": int(self._state["generation"]),
                "connection_epoch": self._connection_epoch,
                "user_id": str(paired["user_id"]),
                "user_name": str(paired["user_name"]),
            }

    @contextmanager
    def credential_scope(
        self,
        *,
        generation: int,
        connection_epoch: int,
        user_id: str,
    ):
        with self._setup_lock:
            self._require_credential_scope(
                generation,
                user_id,
                connection_epoch=connection_epoch,
            )
            yield

    def credential_generation(self) -> int:
        with self._state_lock:
            return int(self._state["generation"])

    @_serialized_setup
    def stage_card(self, value: str | dict[str, object]) -> dict[str, object]:
        self._require_available()
        normalized = parse_card(value)
        with self._state_lock:
            if self._state["paired"] is not None:
                raise PcClientError(
                    "already_configured",
                    409,
                    "This Windows profile already has a paired BASS device.",
                )
            if self._state["pending_commission"] is not None:
                raise PcClientError(
                    "commission_retry_required",
                    409,
                    "Retry the saved commissioning request before importing another card.",
                )
            self._staged_card = normalized
            self._candidates.clear()
        self.disconnect()
        return {
            "ok": True,
            "card": {
                "version": normalized["version"],
                "purpose": normalized["purpose"],
                "device_id": normalized["device_id"],
            },
            "phase": "needs_scan",
        }

    @_serialized_setup
    def scan(self) -> dict[str, object]:
        self._require_available()
        with self._transport_lock:
            with self._state_lock:
                current_client = self._client
            if current_client is not None and not current_client.is_reusable:
                stale_client = self._take_client()
                self._remember_connection_error()
                if stale_client is not None:
                    try:
                        self._submit(stale_client.close())
                    except Exception:
                        pass
            try:
                discovered = self._submit(BleApiClient.discover())
            except Exception as exc:
                raise self._transport_error(exc, "BLE scan failed") from exc
        candidates: dict[str, DiscoveredDevice] = {}
        summaries: list[dict[str, object]] = []
        for device in discovered:
            candidate_id = secrets.token_urlsafe(18)
            candidates[candidate_id] = device
            summaries.append(
                {
                    "candidate_id": candidate_id,
                    "name": device.name,
                    "rssi": None,
                }
            )
        with self._state_lock:
            self._candidates = candidates
        return {"ok": True, "devices": summaries, "phase": self.status()["phase"]}

    @_serialized_setup
    def connect(self, candidate_id: str) -> dict[str, object]:
        self._require_available()
        if not isinstance(candidate_id, str) or not candidate_id:
            raise PcClientError("invalid_request", 400, "Select a discovered device.")
        with self._state_lock:
            candidate = self._candidates.get(candidate_id)
            identity_values = self._connection_identity()
        if candidate is None:
            raise PcClientError(
                "scan_expired",
                409,
                "The discovery result expired. Scan again and select the device.",
            )
        identity = PinnedIdentity(
            str(identity_values["device_id"]),
            str(identity_values["tls_certificate_sha256"]),
        )
        with self._transport_lock:
            previous = self._take_client()
            if previous is not None:
                try:
                    self._submit(previous.close())
                except Exception:
                    pass
            new_client: BleApiClient | None = None
            stage = "Bluetooth GATT connection"
            try:
                new_client = self._submit(BleApiClient.connect(candidate, identity))
                stage = "Pi host verification"
                self._submit(new_client.bootstrap())
                with self._state_lock:
                    paired = deepcopy(self._state["paired"])
                if isinstance(paired, dict):
                    stage = "Saved pairing authentication"
                    authenticated = self._submit(
                        new_client.authenticate(str(paired["device_token"]))
                    )
                    self._validate_authenticated_identity(authenticated, paired)
                    self._refresh_paired_user(authenticated)
            except Exception as exc:
                if new_client is not None:
                    try:
                        self._submit(new_client.close())
                    except Exception:
                        pass
                error = self._staged_transport_error(exc, stage)
                self._remember_connection_error(error)
                raise error from exc
            with self._state_lock:
                self._client = new_client
                self._connection_epoch += 1
                self._connection_error = None
                self._connected_name = candidate.name
        current = self.status()
        return {
            "ok": True,
            "connected": True,
            "device": current["device"],
            "phase": current["phase"],
        }

    @_serialized_setup
    def disconnect(self) -> dict[str, object]:
        with self._transport_lock:
            client = self._take_client()
            if client is not None:
                try:
                    self._submit(client.close())
                except Exception:
                    pass
            with self._state_lock:
                self._connection_epoch += 1
                self._connection_error = None
        return {"ok": True, "phase": self.status()["phase"]}

    @_serialized_setup
    def commission(
        self,
        *,
        pin: object,
        device_name: object,
        name: object = None,
    ) -> dict[str, object]:
        self._require_available()
        validated_pin = self._pin(pin)
        validated_device_name = self._text(device_name, "device_name")
        with self._state_lock:
            if self._state["paired"] is not None:
                raise PcClientError(
                    "already_configured", 409, "This Windows profile is already paired."
                )
            if self._state["pending_commission"] is not None:
                raise PcClientError(
                    "commission_retry_required",
                    409,
                    "Retry the saved commissioning request with the original PIN.",
                )
            card = deepcopy(self._staged_card)
            connected_name = self._connected_name or "BASS"
        if card is None:
            raise PcClientError("card_required", 409, "Import a commissioning card first.")
        purpose = str(card["purpose"])
        if purpose == "activation":
            validated_name = self._text(name, "name")
        elif name not in {None, ""}:
            raise PcClientError(
                "invalid_request", 400, "This card does not accept an owner name."
            )
        else:
            validated_name = ""
        body = self._new_commission_body(
            card,
            device_name=validated_device_name,
            name=validated_name,
        )
        pending = {
            "card": card,
            "body": body,
            "host_name": connected_name,
        }
        with self._state_lock:
            self._require_connected()
            self._state["pending_commission"] = pending
            self._persist_state()
        return self._send_pending_commission(validated_pin)

    @_serialized_setup
    def retry_commission(self, *, pin: object) -> dict[str, object]:
        self._require_available()
        validated_pin = self._pin(pin)
        with self._state_lock:
            if not isinstance(self._state["pending_commission"], dict):
                raise PcClientError(
                    "no_pending_commission", 409, "There is no saved request to retry."
                )
            self._require_connected()
        return self._send_pending_commission(validated_pin)

    @_serialized_setup
    def session_login(
        self,
        pin: object,
        *,
        expected_generation: int,
        expected_connection_epoch: int,
        expected_user_id: str,
    ) -> tuple[int, dict[str, object]]:
        validated_pin = self._pin(pin)
        with self._transport_lock:
            self._require_credential_scope(
                expected_generation,
                expected_user_id,
                connection_epoch=expected_connection_epoch,
            )
            client = self._connected_client()
            try:
                response = self._submit(client.session_login(validated_pin))
            except Exception as exc:
                error = self._transport_error(exc, "Sign-in over BLE failed")
                self._drop_unusable_client(client, error=error)
                raise error from exc
        self._drop_after_response(client, response)
        payload = self._response_object(response)
        if response.status == 200:
            self._refresh_paired_user(payload)
        return response.status, payload

    @_serialized_setup
    def authorize_recovery_export(
        self,
        pin: object,
        *,
        expected_generation: int,
        expected_connection_epoch: int,
        expected_user_id: str,
    ) -> tuple[int, dict[str, object], dict[str, object] | None]:
        status, payload = self.session_login(
            pin,
            expected_generation=expected_generation,
            expected_connection_epoch=expected_connection_epoch,
            expected_user_id=expected_user_id,
        )
        if status != 200:
            return status, payload, None
        self._require_credential_scope(expected_generation, expected_user_id)
        return status, payload, self.recovery_card()

    def proxy_json(
        self,
        api_request: JsonRequest,
        *,
        expected_generation: int,
        expected_connection_epoch: int,
        expected_user_id: str,
        grant: OperationGrant | None = None,
    ):
        with self._setup_lock:
            with self._transport_lock:
                self._require_credential_scope(
                    expected_generation,
                    expected_user_id,
                    connection_epoch=expected_connection_epoch,
                )
                client = self._connected_client()
                try:
                    if grant is None:
                        response = self._submit(client.request_json(api_request))
                    else:
                        if time.monotonic() >= grant["expires_at"]:
                            raise PcClientError(
                                "invalid_operation_grant",
                                403,
                                "Operation approval is invalid or expired.",
                            )
                        response = self._submit(
                            client.grant_then_request(
                                pin=grant["pin"],
                                action=grant["action"],
                                target_user_id=grant["target_user_id"],
                                request=api_request,
                            )
                        )
                except Exception as exc:
                    error = self._transport_error(
                        exc,
                        "Pi request over BLE failed",
                    )
                    self._drop_unusable_client(client, error=error)
                    raise error from exc
        self._drop_after_response(client, response)
        return response

    def recovery_card(self) -> dict[str, object]:
        with self._state_lock:
            card = self._state["recovery_card"]
            if not isinstance(card, dict):
                raise PcClientError(
                    "recovery_card_unavailable", 404, "No recovery card is available."
                )
            return deepcopy(card)

    @_serialized_setup
    def acknowledge_recovery_card(
        self,
        *,
        expected_generation: int,
        expected_user_id: str,
    ) -> dict[str, object]:
        self._require_credential_scope(expected_generation, expected_user_id)
        with self._state_lock:
            if self._state["recovery_card"] is None:
                return {"ok": True}
            self._state["recovery_card"] = None
            self._persist_state()
        return {"ok": True}

    @_serialized_setup
    def forget(self, *, confirm: object) -> dict[str, object]:
        if confirm != "FORGET":
            raise PcClientError(
                "confirmation_required", 400, "Type FORGET to remove this pairing."
            )
        with self._state_lock:
            if self._state["pending_commission"] is not None:
                raise PcClientError(
                    "commission_retry_required",
                    409,
                    "Resolve the saved commissioning request before forgetting.",
                )
            if self._state["recovery_card"] is not None:
                raise PcClientError(
                    "recovery_card_required",
                    409,
                    "Download and acknowledge the recovery card before forgetting.",
                )
            if self._state["paired"] is None:
                raise PcClientError(
                    "not_configured", 409, "This Windows profile is not paired."
                )
            next_state = self._empty_state()
            next_state["generation"] = int(self._state["generation"]) + 1
            self._store.save(next_state)
            self._state = next_state
            self._staged_card = None
            self._candidates.clear()
        self.disconnect()
        return {"ok": True, "phase": "needs_card"}

    def _send_pending_commission(self, pin: str) -> dict[str, object]:
        with self._state_lock:
            pending = deepcopy(self._state["pending_commission"])
            client = self._require_connected()
        assert isinstance(pending, dict)
        card = pending["card"]
        body = {**pending["body"], "pin": pin}
        purpose = str(card["purpose"])
        path = {
            "pairing_invite": "/api/pairings",
            "activation": "/api/commissioning/claim",
            "recovery": "/api/commissioning/recover",
        }[purpose]
        try:
            with self._transport_lock:
                response = self._submit(
                    client.request_json(
                        JsonRequest("POST", path, body),
                        bearer=False,
                    )
                )
        except Exception as exc:
            error = self._transport_error(exc, "Commissioning over BLE failed")
            self._drop_unusable_client(client, error=error)
            raise CommissionOutcomeUnknown() from exc
        self._drop_after_response(client, response)
        try:
            payload = self._response_object(response)
        except PcClientError as exc:
            raise CommissionOutcomeUnknown() from exc
        if not 200 <= response.status < 300:
            code = str(payload.get("code") or "commission_rejected")
            if code in {"invalid_pairing_invite", "invalid_activation"}:
                self._clear_rejected_pending(pending)
            raise PcClientError(
                code,
                response.status,
                str(payload.get("error") or "The Pi rejected the commissioning request."),
            )
        try:
            self._complete_commission(pending, payload)
        except CommissionOutcomeUnknown:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise CommissionOutcomeUnknown() from exc
        current = self.status()
        return {
            "ok": True,
            "completed": True,
            "connected": current["connected"],
            "connection_error": current["connection_error"],
            "user": current["user"],
            "recovery_download_available": current[
                "recovery_download_available"
            ],
            "phase": current["phase"],
        }

    def _clear_rejected_pending(self, pending: Mapping[str, object]) -> None:
        with self._state_lock:
            if self._state["pending_commission"] != pending:
                return
            next_state = deepcopy(self._state)
            next_state["pending_commission"] = None
            self._store.save(next_state)
            self._state = next_state
            self._staged_card = None
        self.disconnect()

    def _complete_commission(
        self,
        pending: Mapping[str, object],
        payload: Mapping[str, object],
    ) -> None:
        body = pending["body"]
        card = pending["card"]
        if not isinstance(body, dict) or not isinstance(card, dict):
            raise PcClientError("invalid_state", 503, "Saved commissioning state is invalid.")
        if payload.get("request_id") != body["request_id"]:
            raise CommissionOutcomeUnknown()
        user = payload.get("user")
        paired_device_id = payload.get("device_id")
        if (
            not isinstance(user, dict)
            or not isinstance(user.get("id"), str)
            or not user["id"]
            or not isinstance(user.get("name"), str)
            or not user["name"]
            or not isinstance(paired_device_id, str)
            or not paired_device_id
        ):
            raise CommissionOutcomeUnknown()
        paired = {
            "host_device_id": card["device_id"],
            "tls_certificate_sha256": card["tls_certificate_sha256"],
            "device_token": body["device_token"],
            "paired_device_id": paired_device_id,
            "user_id": user["id"],
            "user_name": user["name"],
            "host_name": pending.get("host_name") or "BASS",
        }
        recovery_card = None
        new_recovery_secret = body.get("new_recovery_secret") or body.get(
            "recovery_secret"
        )
        if card["purpose"] in {"activation", "recovery"}:
            recovery_card = {
                "version": 3,
                "device_id": card["device_id"],
                "tls_certificate_sha256": card["tls_certificate_sha256"],
                "purpose": "recovery",
                "recovery_secret": new_recovery_secret,
            }
            parse_card(recovery_card)
        with self._state_lock:
            current = self._state["pending_commission"]
            if current != pending:
                raise PcClientError(
                    "commission_state_changed",
                    409,
                    "The saved commissioning request changed; reconnect and review status.",
                )
            next_state = deepcopy(self._state)
            next_state["generation"] = int(next_state["generation"]) + 1
            next_state["paired"] = paired
            next_state["pending_commission"] = None
            next_state["recovery_card"] = recovery_card
            self._store.save(next_state)
            self._state = next_state
            self._staged_card = None

        with self._state_lock:
            client_if_reusable = self._client
        if client_if_reusable is not None and client_if_reusable.is_reusable:
            try:
                with self._transport_lock:
                    authenticated = self._submit(
                        client_if_reusable.authenticate(str(body["device_token"]))
                    )
                self._validate_authenticated_identity(authenticated, paired)
                self._refresh_paired_user(authenticated)
                self._clear_connection_error()
            except Exception as exc:
                error = self._staged_transport_error(
                    exc,
                    "Paired credential authentication",
                )
                self._drop_unusable_client(
                    client_if_reusable,
                    force=True,
                    error=error,
                )

    @staticmethod
    def _new_commission_body(
        card: Mapping[str, object],
        *,
        device_name: str,
        name: str,
    ) -> dict[str, object]:
        body: dict[str, object] = {
            "request_id": str(uuid.uuid4()),
            "device_name": device_name,
            "device_token": secrets.token_hex(32),
        }
        purpose = card["purpose"]
        if purpose == "pairing_invite":
            body["invite_token"] = card["invite_token"]
        elif purpose == "activation":
            body.update(
                {
                    "activation_secret": card["activation_secret"],
                    "name": name,
                    "recovery_secret": secrets.token_hex(32),
                }
            )
        else:
            body.update(
                {
                    "recovery_secret": card["recovery_secret"],
                    "new_recovery_secret": secrets.token_hex(32),
                }
            )
        return body

    def _connection_identity(self) -> Mapping[str, object]:
        paired = self._state["paired"]
        if isinstance(paired, dict):
            return {
                "device_id": paired["host_device_id"],
                "tls_certificate_sha256": paired["tls_certificate_sha256"],
            }
        pending = self._state["pending_commission"]
        if isinstance(pending, dict):
            return pending["card"]
        if self._staged_card is not None:
            return self._staged_card
        raise PcClientError(
            "card_required", 409, "Import a commissioning card before connecting."
        )

    def _connected_client(self) -> BleApiClient:
        with self._state_lock:
            return self._require_connected()

    def _require_connected(self) -> BleApiClient:
        client = self._client
        if client is None or not client.is_reusable:
            raise PcClientError(
                "ble_not_connected",
                409,
                "Scan for the saved BASS device and reconnect first.",
            )
        return client

    def _take_client(self) -> BleApiClient | None:
        with self._state_lock:
            client = self._client
            self._client = None
            if client is not None:
                self._connection_epoch += 1
            self._connected_name = ""
            return client

    def _drop_unusable_client(
        self,
        client: BleApiClient,
        *,
        force: bool = False,
        error: PcClientError | None = None,
    ) -> None:
        if not force and client.is_reusable:
            return
        with self._state_lock:
            if self._client is client:
                self._client = None
                self._connection_epoch += 1
                if error is not None:
                    self._connection_error = self._error_payload(error)
                elif self._connection_error is None:
                    self._connection_error = self._lost_connection_error()
                self._connected_name = ""
        try:
            self._submit(client.close())
        except Exception:
            pass

    def _submit(self, coroutine, *, allow_closed: bool = False):
        if self._closed and not allow_closed:
            coroutine.close()
            raise PcClientError("client_closed", 503, "The PC BLE client is closed.")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=CONTROLLER_TIMEOUT_SECONDS)
        except concurrent.futures.TimeoutError:
            if future.done():
                return future.result()
            if not future.cancel() and future.done():
                return future.result()
            raise PcClientError(
                "ble_timeout", 504, "The Bluetooth operation timed out."
            ) from None

    def _persist_state(self) -> None:
        self._store.save(self._state)

    def _refresh_paired_user(self, payload: Mapping[str, object]) -> None:
        user = payload.get("user")
        if not isinstance(user, dict) or not isinstance(user.get("name"), str):
            raise PinnedIdentityError("Authenticated account response is malformed")
        name = user["name"]
        if not name or len(name) > MAX_TEXT_LENGTH:
            raise PinnedIdentityError("Authenticated account response is malformed")
        with self._state_lock:
            paired = self._state["paired"]
            if not isinstance(paired, dict):
                raise PinnedIdentityError("Saved pairing is unavailable")
            if paired["user_id"] != user.get("id"):
                raise PinnedIdentityError("Authenticated pairing identity changed")
            if paired["user_name"] == name:
                return
            next_state = deepcopy(self._state)
            next_state["paired"]["user_name"] = name
            self._store.save(next_state)
            self._state = next_state

    def _require_available(self) -> None:
        if self._state_error:
            raise PcClientError("state_unavailable", 503, self._state_error)
        dependency_error = self._dependency_error()
        if dependency_error:
            raise PcClientError("dependency_missing", 503, dependency_error)

    def _require_credential_scope(
        self,
        generation: int,
        user_id: str,
        *,
        connection_epoch: int | None = None,
    ) -> None:
        with self._state_lock:
            paired = self._state["paired"]
            if (
                type(generation) is not int
                or int(self._state["generation"]) != generation
                or (
                    connection_epoch is not None
                    and self._connection_epoch != connection_epoch
                )
                or not isinstance(paired, dict)
                or paired["user_id"] != user_id
            ):
                raise PcClientError(
                    "login_required",
                    401,
                    "The paired account changed. Sign in again.",
                )

    @staticmethod
    def _dependency_error() -> str | None:
        if importlib.util.find_spec("bleak") is None:
            return (
                "Bluetooth support is unavailable. Install the PC BLE requirements "
                "and restart BASS."
            )
        if importlib.util.find_spec("cryptography") is None:
            return (
                "Pinned HTTPS support is unavailable. Install the PC BLE requirements "
                "and restart BASS."
            )
        return None

    def _phase(
        self,
        *,
        paired: object,
        pending: object,
        connected: bool,
        dependency_error: str | None,
    ) -> str:
        if dependency_error:
            return "dependency_missing"
        if paired is not None:
            return "ready" if connected else "ready_disconnected"
        if pending is not None:
            return "commission_retry"
        if self._staged_card is None:
            return "needs_card"
        if not self._candidates:
            return "needs_scan" if not connected else "needs_commission"
        return "needs_commission" if connected else "needs_connection"

    @staticmethod
    def _pin(value: object) -> str:
        if not isinstance(value, str) or PIN_PATTERN.fullmatch(value) is None:
            raise PcClientError(
                "invalid_pin", 400, "PIN must contain exactly six ASCII digits."
            )
        return value

    @staticmethod
    def _text(value: object, field: str) -> str:
        if not isinstance(value, str):
            raise PcClientError("invalid_request", 400, f"{field} is required.")
        normalized = value.strip()
        if not normalized or len(normalized) > MAX_TEXT_LENGTH:
            raise PcClientError(
                "invalid_request",
                400,
                f"{field} must contain 1 to {MAX_TEXT_LENGTH} characters.",
            )
        return normalized

    @staticmethod
    def _validate_authenticated_identity(
        payload: Mapping[str, object], paired: Mapping[str, object]
    ) -> None:
        user = payload.get("user")
        if (
            payload.get("device_id") != paired["paired_device_id"]
            or not isinstance(user, dict)
            or user.get("id") != paired["user_id"]
        ):
            raise PinnedIdentityError("Authenticated pairing identity changed")

    @staticmethod
    def _response_object(response) -> dict[str, object]:
        try:
            payload = response.json()
        except Exception as exc:
            raise PcClientError(
                "invalid_pi_response", 502, "The Pi returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise PcClientError(
                "invalid_pi_response", 502, "The Pi returned an invalid response."
            )
        return payload

    @staticmethod
    def _transport_error(exc: Exception, fallback: str) -> PcClientError:
        if isinstance(exc, PcClientError):
            return exc
        if isinstance(exc, BleDependencyError):
            return PcClientError("dependency_missing", 503, str(exc))
        if isinstance(exc, PinnedIdentityError):
            return PcClientError("device_identity_mismatch", 409, str(exc))
        if isinstance(exc, BleRequestQueueFullError):
            return PcClientError("client_busy", 429, str(exc))
        if isinstance(exc, BleHttpStatusError):
            try:
                payload = exc.response.json()
            except Exception:
                payload = None
            if isinstance(payload, dict):
                code = payload.get("code")
                message = payload.get("error") or payload.get("message")
                if isinstance(code, str) and isinstance(message, str):
                    return PcClientError(code, exc.response.status, message)
            return PcClientError("pi_request_failed", 502, str(exc))
        if isinstance(exc, TimeoutError):
            return PcClientError("ble_timeout", 504, str(exc) or fallback)
        if isinstance(exc, BleClientError):
            return PcClientError("ble_transport_error", 502, str(exc))
        return PcClientError("ble_transport_error", 502, fallback)

    @classmethod
    def _staged_transport_error(
        cls,
        exc: Exception,
        stage: str,
    ) -> PcClientError:
        fallback = (
            "The Bluetooth operation timed out."
            if isinstance(exc, TimeoutError)
            else "Bluetooth operation failed."
        )
        error = cls._transport_error(exc, fallback)
        return PcClientError(
            error.code,
            error.status,
            f"{stage} failed: {error}",
        )

    def _drop_after_response(
        self,
        client: BleApiClient,
        response: CommittedResponse,
    ) -> None:
        error = None
        if not response.transport_reusable:
            detail = response.teardown_error or "Bluetooth transport teardown failed."
            error = PcClientError(
                "ble_transport_error",
                502,
                "The Pi response completed, but the Bluetooth connection could "
                f"not be reused: {detail}",
            )
        self._drop_unusable_client(
            client,
            force=not response.transport_reusable,
            error=error,
        )

    def _remember_connection_error(self, error: PcClientError | None = None) -> None:
        with self._state_lock:
            if error is not None:
                self._connection_error = self._error_payload(error)
            elif self._connection_error is None:
                self._connection_error = self._lost_connection_error()

    def _clear_connection_error(self) -> None:
        with self._state_lock:
            self._connection_error = None

    @staticmethod
    def _error_payload(error: PcClientError) -> dict[str, str]:
        return {"code": error.code, "message": str(error)}

    @staticmethod
    def _lost_connection_error() -> dict[str, str]:
        return {
            "code": "ble_disconnected",
            "message": "The Bluetooth connection was lost. Scan and reconnect "
            "to the saved Pi.",
        }

    @classmethod
    def _normalize_state(cls, value: object) -> dict[str, object]:
        if value is None:
            return cls._empty_state()
        if not isinstance(value, dict) or set(value) != {
            "version",
            "generation",
            "paired",
            "pending_commission",
            "recovery_card",
        }:
            raise ValueError("Saved PC client state has an unsupported shape.")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Saved PC client state has an unsupported version.")
        if type(value["generation"]) is not int:
            raise ValueError("Saved PC client state has an unsupported version.")
        if value["generation"] < 0:
            raise ValueError("Saved PC client generation is invalid.")
        paired = value["paired"]
        pending = value["pending_commission"]
        recovery = value["recovery_card"]
        if paired is not None:
            cls._validate_paired(paired)
        if pending is not None:
            cls._validate_pending(pending)
        if paired is not None and pending is not None:
            raise ValueError("Saved PC client state cannot be paired and pending.")
        if recovery is not None:
            normalized_recovery = parse_card(recovery)
            if normalized_recovery["purpose"] != "recovery" or paired is None:
                raise ValueError("Saved recovery card is invalid.")
            if normalized_recovery["device_id"] != paired["host_device_id"]:
                raise ValueError("Saved recovery card belongs to another device.")
            value["recovery_card"] = normalized_recovery
        return deepcopy(value)

    @staticmethod
    def _validate_paired(value: object) -> None:
        required = {
            "host_device_id",
            "tls_certificate_sha256",
            "device_token",
            "paired_device_id",
            "user_id",
            "user_name",
            "host_name",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("Saved pairing is invalid.")
        if not isinstance(value["host_device_id"], str) or not isinstance(
            value["tls_certificate_sha256"], str
        ):
            raise ValueError("Saved pairing host identity is invalid.")
        PinnedIdentity(value["host_device_id"], value["tls_certificate_sha256"])
        if not isinstance(value["device_token"], str) or not TOKEN_PATTERN.fullmatch(
            value["device_token"]
        ):
            raise ValueError("Saved device credential is invalid.")
        for field in ("paired_device_id", "user_id", "user_name", "host_name"):
            if not isinstance(value[field], str) or not value[field]:
                raise ValueError("Saved pairing identity is invalid.")

    @staticmethod
    def _validate_pending(value: object) -> None:
        if not isinstance(value, dict) or set(value) != {"card", "body", "host_name"}:
            raise ValueError("Saved commissioning request is invalid.")
        card = parse_card(value["card"])
        body = value["body"]
        if not isinstance(body, dict) or "pin" in body:
            raise ValueError("Saved commissioning request is invalid.")
        common = {"request_id", "device_name", "device_token"}
        purpose_fields = {
            "pairing_invite": {"invite_token"},
            "activation": {"activation_secret", "name", "recovery_secret"},
            "recovery": {"recovery_secret", "new_recovery_secret"},
        }
        if set(body) != common | purpose_fields[card["purpose"]]:
            raise ValueError("Saved commissioning request is invalid.")
        try:
            if str(uuid.UUID(body["request_id"])) != body["request_id"]:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError("Saved request ID is invalid.") from exc
        for field in ("device_token", *purpose_fields[card["purpose"]]):
            if field in {"name"}:
                continue
            if not isinstance(body[field], str) or not HEX_SECRET_PATTERN.fullmatch(
                body[field]
            ):
                raise ValueError("Saved commissioning credential is invalid.")
        if body.get({
            "pairing_invite": "invite_token",
            "activation": "activation_secret",
            "recovery": "recovery_secret",
        }[card["purpose"]]) != card[{
            "pairing_invite": "invite_token",
            "activation": "activation_secret",
            "recovery": "recovery_secret",
        }[card["purpose"]]]:
            raise ValueError("Saved commissioning card and request do not match.")
        for field in ("device_name", "host_name"):
            item = body[field] if field == "device_name" else value[field]
            if not isinstance(item, str) or not item or len(item) > MAX_TEXT_LENGTH:
                raise ValueError("Saved commissioning name is invalid.")
        if card["purpose"] == "activation":
            name = body["name"]
            if not isinstance(name, str) or not name or len(name) > MAX_TEXT_LENGTH:
                raise ValueError("Saved owner name is invalid.")
