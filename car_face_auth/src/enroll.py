"""Retired standalone enrollment entrypoint.

Enrollment now runs through the authenticated BASS Wireless host so one
runtime owns face templates and camera access.
"""

from __future__ import annotations

import sys


RETIREMENT_MESSAGE = (
    "The standalone enrollment CLI has been retired. Start the authenticated "
    "BASS Wireless host with `python bass_wireless.py`, then use the paired "
    "Android app or local dashboard to enroll a face."
)


def main() -> int:
    print(RETIREMENT_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
