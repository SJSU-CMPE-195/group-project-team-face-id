"""Safe, human-readable projections of persisted audit events."""

from __future__ import annotations

import json
from typing import Any


_SUCCESS_RESULTS = frozenset({"ok", "success", "true", "pass", "1"})
_DENIED_RESULTS = frozenset({"denied", "rejected", "unauthorized"})
_CANCELLED_STAGES = frozenset(
    {"scan_cancelled", "enroll_cancelled", "enrollment_cancelled"}
)
_STARTED_STAGES = frozenset({"scan_started", "enrollment_started"})
_TIMING_STATES = frozenset(
    {"granted", "denied", "timeout", "cancelled", "error"}
)

_ACTION_LABELS = {
    "device.lock": "locking the device",
    "device.reset": "resetting the device",
    "device.revoke": "revoking a paired device",
    "enrollment.start": "starting face enrollment",
    "ignition.stop": "stopping the ignition",
    "pairing.invite": "creating a phone invitation",
    "scan.ignition": "starting the ignition face scan",
    "scan.unlock": "starting the unlock face scan",
    "settings.update": "updating settings",
    "user.access": "changing face access",
    "user.create": "creating an account",
    "user.delete": "removing an account",
    "user.pin": "changing a PIN",
    "user.role": "changing a role",
}


def format_event(
    stage: str,
    result: str,
    detail: str,
    *,
    actor_name: str | None = None,
    subject_name: str | None = None,
) -> dict[str, str]:
    """Return display text without exposing untrusted audit detail."""

    stage = stage.strip().lower() if isinstance(stage, str) else ""
    result = result.strip().lower() if isinstance(result, str) else ""
    detail = detail if isinstance(detail, str) else ""
    actor = _name(actor_name)
    subject = _name(subject_name)
    outcome = _outcome(stage, result, detail)

    if stage == "scan_started":
        return _event(
            "Face scan started",
            _started("a face scan", actor, subject),
            "info",
        )
    if stage == "enrollment_started":
        return _event(
            "Face enrollment started",
            _started("face enrollment", actor, subject),
            "info",
        )
    if stage in _CANCELLED_STAGES:
        action = "Face scan" if stage == "scan_cancelled" else "Face enrollment"
        return _event(
            f"{action} cancelled",
            _for_subject(f"{action} was cancelled", subject),
            "cancelled",
        )
    if stage == "face_scan":
        return _face_scan_event(outcome, detail, subject)
    if stage == "scan_timing":
        return _scan_timing_event(detail, subject)
    if stage == "enrollment":
        if outcome == "success":
            summary = _for_subject("Face enrollment completed", subject)
            title = "Face enrollment"
        elif outcome == "cancelled":
            summary = _for_subject("Face enrollment was cancelled", subject)
            title = "Face enrollment cancelled"
        else:
            summary = _for_subject("Face enrollment did not complete", subject)
            title = "Face enrollment"
        return _event(title, summary, outcome)
    if stage == "enroll_embedding":
        return _event(
            "Face enrollment completed",
            _for_subject("Face template saved", subject),
            outcome,
        )
    if stage == "delete_embedding":
        return _event(
            "Face enrollment removed",
            _for_subject("Face template removed", subject),
            outcome,
        )
    if stage == "enroll":
        return _event(
            "Account created",
            _for_subject("Account created", subject),
            outcome,
        )
    if stage == "delete_user":
        return _event(
            "Account removed",
            _for_subject("Account removed", subject),
            outcome,
        )
    if stage == "access_change":
        detail_lower = detail.strip().lower()
        if detail_lower.startswith("granted for "):
            action = "Face access enabled"
        elif detail_lower.startswith("revoked for "):
            action = "Face access disabled"
        else:
            action = "Face access changed"
        return _event(
            "Face access changed",
            _for_subject(action, subject),
            outcome,
        )
    if stage in {"unlock", "lock"}:
        action = "Unlock" if stage == "unlock" else "Lock"
        return _event(
            f"{action} command sent",
            _for_subject(f"{action} command sent", subject),
            outcome,
        )
    if stage == "ignition":
        action = "start" if detail.lower().startswith("start:") else "stop"
        return _event(
            f"Ignition {action} command sent",
            _for_subject(f"Ignition {action} command sent", subject),
            outcome,
        )
    if stage == "simulated_actuator":
        command = detail.strip().upper()
        label = (
            command
            if command in {"LOCK", "UNLOCK", "START", "STOP"}
            else "actuator"
        )
        return _event(
            "Simulator command sent",
            f"{label.capitalize()} command sent to the simulator.",
            "info",
        )
    if stage == "settings":
        return _event(
            "Settings updated",
            _by_actor("Settings updated", actor),
            outcome,
        )
    if stage in {"local_login", "session_login"}:
        return _login_event(outcome, subject)
    if stage == "local_logout":
        return _event(
            "Signed out",
            _for_subject("Signed out", subject),
            outcome,
        )
    if stage == "security_user_created":
        return _event(
            "Account secured",
            _for_subject("PIN protection added", subject),
            outcome,
        )
    if stage == "security_pin_changed":
        return _event(
            "PIN changed",
            _for_subject("PIN changed", subject),
            outcome,
        )
    if stage in {"security_role_changed", "security_role_unchanged"}:
        verb = (
            "Role changed"
            if stage == "security_role_changed"
            else "Role confirmed"
        )
        role_by_detail = {
            "admin=0": "User role saved",
            "admin=1": "Administrator role saved",
        }
        summary = role_by_detail.get(detail.strip(), "Role saved")
        return _event(
            verb,
            _for_subject(summary, subject),
            outcome,
        )
    if stage in {"security_initial_admin", "security_admin_recovered"}:
        title = (
            "Administrator initialized"
            if stage == "security_initial_admin"
            else "Administrator recovered"
        )
        return _event(title, _for_subject(title, subject), outcome)
    if stage == "pairing_invite":
        return _event(
            "Phone invitation created",
            _for_subject("Phone invitation created", subject),
            outcome,
        )
    if stage == "device_pairing":
        summary = (
            _for_subject("Phone paired", subject)
            if outcome == "success"
            else _for_subject("Phone pairing was denied", subject)
        )
        return _event("Phone pairing", summary, outcome)
    if stage == "device_revoked":
        return _event(
            "Phone access revoked",
            _for_subject("Phone access revoked", subject),
            outcome,
        )
    if stage in {"product_claim", "owner_recovery"}:
        title = (
            "Product activated"
            if stage == "product_claim"
            else "Owner access recovered"
        )
        return _event(title, _for_subject(title, subject), outcome)
    if stage == "tls_identity_rotation":
        return _event(
            "Device security renewed",
            "Device security was renewed and existing sessions were revoked.",
            outcome,
        )
    if stage == "face_verification":
        summary = (
            _for_subject("Face verification succeeded", subject)
            if outcome == "success"
            else _for_subject("Face verification failed", subject)
        )
        return _event("Face verification", summary, outcome)
    if stage in {"operation_authorization", "operation_grant_consumed"}:
        return _authorization_event(stage, outcome, detail, actor)

    return _event("System event", _generic_summary(outcome), outcome)


def _outcome(stage: str, result: str, detail: str) -> str:
    if stage in _CANCELLED_STAGES:
        return "cancelled"
    if stage == "enrollment" and detail.strip().casefold() in {
        "enroll cancelled.",
        "enrollment cancelled.",
    }:
        return "cancelled"
    if stage in _STARTED_STAGES:
        return "info"
    if stage == "scan_timing":
        payload = _timing_payload(detail)
        state = _timing_state(payload)
        if state == "granted":
            return "success"
        if state in {"denied", "timeout"}:
            return "denied"
        if state == "cancelled":
            return "cancelled"
        if state == "error":
            return "error"
    if result in _SUCCESS_RESULTS:
        return "success"
    if result in _DENIED_RESULTS:
        return "denied"
    if stage == "face_scan" and _is_denial_reason(detail):
        return "denied"
    if result in {"cancelled", "canceled"}:
        return "cancelled"
    if result in {"error", "fail", "failed", "false", "0"}:
        return "error"
    return "info"


def _face_scan_event(
    outcome: str,
    detail: str,
    subject: str | None,
) -> dict[str, str]:
    lowered = detail.lower()
    if outcome == "success":
        summary = _recognized(subject, "the unlock command was sent")
        return _event("Face access granted", summary, outcome)
    if lowered.strip() == "access denied: face access is disabled.":
        summary = _recognized(subject, "face access is disabled", denied=True)
        return _event("Face access denied", summary, "denied")
    if lowered.strip() == "access was revoked before the scan completed.":
        summary = _for_subject(
            "Face access changed before the scan completed",
            subject,
        )
        return _event("Face access denied", summary, "denied")
    if "timed out" in lowered or "timeout" in lowered:
        return _event(
            "Face scan timed out",
            _for_subject("Face scan timed out", subject),
            "denied",
        )
    if outcome == "denied":
        return _event(
            "Face access denied",
            _for_subject("Face verification failed", subject),
            outcome,
        )
    return _event(
        "Face scan failed",
        _for_subject("Face scan could not be completed", subject),
        "error",
    )


def _scan_timing_event(detail: str, subject: str | None) -> dict[str, str]:
    payload = _timing_payload(detail)
    state = _timing_state(payload)
    outcome = _outcome("scan_timing", "", detail)
    purpose = "Ignition" if payload.get("purpose") == "ignition" else "Unlock"
    duration = payload.get("duration_ms")
    duration_text = ""
    valid_duration = (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and 0 <= duration <= 3_600_000
    )
    if valid_duration:
        duration_text = f" in {duration / 1000:.1f} seconds"
    state_text = {
        "granted": "completed",
        "denied": "was denied",
        "timeout": "timed out",
        "cancelled": "was cancelled",
        "error": "failed",
    }.get(state, "finished")
    summary = _for_subject(
        f"{purpose} face scan {state_text}{duration_text}",
        subject,
    )
    return _event("Face scan timing", summary, outcome)


def _login_event(outcome: str, subject: str | None) -> dict[str, str]:
    if outcome == "success":
        return _event("Signed in", _for_subject("Signed in", subject), outcome)
    return _event(
        "Sign-in denied",
        _for_subject("Sign-in was denied", subject),
        "denied",
    )


def _authorization_event(
    stage: str,
    outcome: str,
    detail: str,
    actor: str | None,
) -> dict[str, str]:
    action = detail.partition(":")[0].strip()
    label = _ACTION_LABELS.get(action, "performing an operation")
    if stage == "operation_grant_consumed":
        return _event(
            "Authorization used",
            _by_actor(f"Authorization used for {label}", actor),
            outcome,
        )
    if outcome == "success":
        return _event(
            "Operation authorized",
            _by_actor(f"Authorized {label}", actor),
            outcome,
        )
    return _event(
        "Authorization denied",
        _by_actor(f"Authorization denied for {label}", actor),
        "denied",
    )


def _timing_payload(detail: str) -> dict[str, Any]:
    try:
        payload = json.loads(detail)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _timing_state(payload: dict[str, Any]) -> str | None:
    state = payload.get("state")
    if not isinstance(state, str):
        return None
    state = state.strip().lower()
    return state if state in _TIMING_STATES else None


def _is_denial_reason(detail: str) -> bool:
    lowered = detail.lower()
    return any(
        phrase in lowered
        for phrase in (
            "access is disabled",
            "access was revoked",
            "did not match",
            "no authorized user",
            "timed out",
            "timeout",
        )
    )


def _started(action: str, actor: str | None, subject: str | None) -> str:
    if actor and subject and actor != subject:
        return f"{actor} started {action} for {subject}."
    if subject:
        return f"{action.capitalize()} started for {subject}."
    if actor:
        return f"{actor} started {action}."
    return f"{action.capitalize()} started."


def _for_subject(text: str, subject: str | None) -> str:
    return f"{text} for {subject}." if subject else f"{text}."


def _recognized(
    subject: str | None,
    result: str,
    *,
    denied: bool = False,
) -> str:
    if subject:
        conjunction = "but" if denied else "and"
        return f"{subject} was recognized, {conjunction} {result}."
    if denied:
        return f"A face was recognized, but {result}."
    return f"Face recognized; {result}."


def _by_actor(text: str, actor: str | None) -> str:
    return f"{actor}: {text.lower()}." if actor else f"{text}."


def _generic_summary(outcome: str) -> str:
    return {
        "success": "The event completed successfully.",
        "denied": "The request was denied.",
        "cancelled": "The operation was cancelled.",
        "error": "The event could not be completed.",
        "info": "The event was recorded.",
    }[outcome]


def _event(title: str, summary: str, outcome: str) -> dict[str, str]:
    return {"title": title, "summary": summary, "outcome": outcome}


def _name(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not value.isprintable():
        return None
    return value[:80]
