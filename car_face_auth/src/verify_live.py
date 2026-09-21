"""Retired standalone face-verification and actuator entrypoint.

Verification now runs through the authenticated BASS Wireless host so one
runtime owns the Pi camera, authorization checks, and ESP32 commands.
"""

from __future__ import annotations

import sys


RETIREMENT_MESSAGE = (
    "The standalone live-verification CLI has been retired. Start the "
    "authenticated BASS Wireless host with `python bass_wireless.py`, then use "
    "the paired Android app or local dashboard to request face verification."
)


def main() -> int:
    print(RETIREMENT_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
