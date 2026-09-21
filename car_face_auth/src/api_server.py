"""Retired unauthenticated Face API entrypoint."""

RETIREMENT_MESSAGE = (
    "The unauthenticated standalone Face API has been retired. "
    "Run bass_wireless.py and use the paired HTTPS API instead."
)

raise RuntimeError(RETIREMENT_MESSAGE)
