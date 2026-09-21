"""Retired enrollment debugger; it bypassed host authorization and used pickle."""

import sys


def main() -> int:
    print(
        "The standalone enrollment debugger has been retired. "
        "Use the administrator-authorized enrollment flow in bass_wireless.py.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
