#!/usr/bin/env python3
"""Administrative CLI for the FaceID device.

Run on the Pi itself.  This is the trusted local path that bootstraps the very
first administrator: before anyone is paired there is no session to authorize
an API call, so the break-in has to happen from the console.

    python manage.py create-admin "Ada"     # first administrator
    python manage.py add-user "Bob"         # ordinary driver
    python manage.py pair "Bob"             # one-time code for Bob's browser
    python manage.py list-users
    python manage.py revoke "Bob"           # sign all of Bob's devices out
"""

from __future__ import annotations

import argparse
import sys

from db import init_db
import db_api


def _find(name: str):
    user = db_api.get_user_by_name(name)
    if not user:
        print(f"No active user named {name!r}.", file=sys.stderr)
        raise SystemExit(1)
    return user


def cmd_create_admin(args) -> int:
    existing = db_api.get_user_by_name(args.name)
    if existing:
        result = db_api.set_user_role(existing["id"], "ADMIN")
        if not result.get("ok"):
            print(result.get("error", "could not promote user"), file=sys.stderr)
            return 1
        print(f"Promoted existing user {args.name!r} to ADMIN.")
        user_id = existing["id"]
    else:
        created = db_api.add_user(args.name)
        if created.get("ok") is False:
            print(created.get("error", "could not create user"), file=sys.stderr)
            return 1
        db_api.set_user_role(created["id"], "ADMIN")
        print(f"Created administrator {args.name!r}.")
        user_id = created["id"]

    issued = db_api.create_pairing_code(user_id)
    _print_code(args.name, issued)
    return 0


def cmd_add_user(args) -> int:
    created = db_api.add_user(args.name)
    if created.get("ok") is False:
        print(created.get("error", "could not create user"), file=sys.stderr)
        return 1
    print(f"Created user {args.name!r} (role USER).")
    return 0


def cmd_pair(args) -> int:
    user = _find(args.name)
    issued = db_api.create_pairing_code(user["id"])
    if not issued.get("ok"):
        print(issued.get("error", "could not create pairing code"), file=sys.stderr)
        return 1
    _print_code(args.name, issued)
    return 0


def cmd_list_users(args) -> int:
    users = db_api.list_users_for_ui()
    if not users:
        print("No active users.")
        return 0
    width = max(len(u["name"]) for u in users)
    print(f"{'NAME'.ljust(width)}  ROLE   FACE  ID")
    for u in users:
        face = "yes " if u["faceEnrolled"] else "no  "
        print(f"{u['name'].ljust(width)}  {u['role']:<5}  {face}  {u['id']}")
    return 0


def cmd_revoke(args) -> int:
    user = _find(args.name)
    result = db_api.revoke_all_sessions_for_user(user["id"])
    print(f"Revoked {result.get('revoked', 0)} session(s) for {args.name!r}.")
    return 0


def _print_code(name: str, issued: dict) -> None:
    minutes = max(1, int(issued.get("expires_in", 300)) // 60)
    print()
    print(f"  Pairing code for {name}:")
    print(f"      {issued['code']}")
    print()
    print(f"  Single use, expires in {minutes} minutes.")
    print("  Enter it in the app on the device you are pairing.")
    print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-admin", help="create or promote an administrator, then pair them")
    p.add_argument("name")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("add-user", help="create an ordinary driver")
    p.add_argument("name")
    p.set_defaults(func=cmd_add_user)

    p = sub.add_parser("pair", help="mint a one-time pairing code")
    p.add_argument("name")
    p.set_defaults(func=cmd_pair)

    p = sub.add_parser("list-users", help="list active users and roles")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("revoke", help="revoke every session for a user")
    p.add_argument("name")
    p.set_defaults(func=cmd_revoke)

    args = parser.parse_args(argv)
    init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
