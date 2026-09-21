"""Shared setting names and bounds for API, persistence, and runtime policy."""

BOOLEAN_SETTINGS = frozenset({"liveness", "failLockout"})
INTEGER_SETTING_RANGES = {
    "autoRelockSeconds": (0, 600),
    "ignitionAutoStopSeconds": (0, 1800),
    "promptAutoLockSeconds": (0, 600),
    "lockoutAfter": (1, 20),
}
