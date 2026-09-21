#!/usr/bin/env python3
"""Recover an existing administrator locally after stopping the BASS host."""

import argparse
import getpass
import os
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()
    database = args.db.expanduser().resolve(strict=True)
    config = args.config.expanduser().resolve(strict=True)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ["FACEID_DB_PATH"] = str(database)
    from db import get_conn
    from wireless.auth_secret import load_auth_secret
    from wireless.security import SecurityStore

    secret = load_auth_secret(config, credentials_exist=True)
    first = getpass.getpass("New six-digit account PIN: ")
    second = getpass.getpass("Confirm PIN: ")
    if first != second:
        raise ValueError("PIN entries differ; no change was made.")
    store = SecurityStore(get_conn, secret)
    store.recover_admin(args.user, first)
    print("Administrator recovered. Existing devices and grants for that user are revoked.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Recovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
