#!/usr/bin/env python3
"""Create a separate SQLite copy with legacy face templates converted safely."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from car_face_auth.src.embedding_format import (  # noqa: E402
    MAGIC,
    EmbeddingFormatError,
    decode_embeddings,
    encode_embeddings,
)
from wireless.security_schema import (  # noqa: E402
    USER_TRIGGER_NAMES,
    USER_TRIGGER_STATEMENTS,
)


def _normalize_trigger_sql(sql: str) -> str:
    normalized = " ".join(sql.strip().rstrip(";").split())
    return re.sub(
        r"^CREATE TRIGGER(?: IF NOT EXISTS)? ",
        "CREATE TRIGGER ",
        normalized,
        count=1,
    )


def _known_user_triggers() -> dict[str, str]:
    known = {}
    for statement in USER_TRIGGER_STATEMENTS:
        normalized = " ".join(statement.strip().split())
        match = re.match(
            r"^CREATE TRIGGER IF NOT EXISTS ([A-Za-z_][A-Za-z0-9_]*)\b",
            normalized,
        )
        if (
            match
            and match.group(1) in USER_TRIGGER_NAMES
            and re.search(r"\bON users\b", normalized)
        ):
            known[match.group(1)] = _normalize_trigger_sql(statement)
    return known


KNOWN_USER_TRIGGERS = _known_user_triggers()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(path: Path) -> dict[str, dict[str, object] | None]:
    """Fingerprint SQLite's durable database files, excluding mutable SHM state."""
    wal_path = Path(f"{path}-wal")

    def fingerprint_file(candidate: Path) -> dict[str, object] | None:
        if not candidate.exists():
            return None
        return {
            "size": candidate.stat().st_size,
            "sha256": _sha256(candidate),
        }

    return {
        "database": fingerprint_file(path),
        "wal": fingerprint_file(wal_path),
    }


def _open_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _validated_user_triggers(connection: sqlite3.Connection) -> dict[str, str]:
    triggers = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='trigger' AND tbl_name='users' ORDER BY name"
    ).fetchall()
    validated = {}
    for trigger in triggers:
        name = trigger["name"]
        sql = trigger["sql"]
        if (
            name not in KNOWN_USER_TRIGGERS
            or not isinstance(sql, str)
            or _normalize_trigger_sql(sql) != KNOWN_USER_TRIGGERS[name]
        ):
            raise ValueError("source database has unsupported triggers on users")
        validated[name] = sql
    return validated


def _require_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)")
    }
    if not {"id", "face_encoding"}.issubset(columns):
        raise ValueError("source database does not contain the expected users schema")
    _validated_user_triggers(connection)


def _user_table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: connection.execute(
            f'SELECT COUNT(*) FROM "{name.replace(chr(34), chr(34) * 2)}"'
        ).fetchone()[0]
        for name in names
    }


def _legacy_pickle_to_safe(blob: bytes) -> bytes:
    # This import is intentionally confined to the explicit offline migration path.
    import pickle

    return encode_embeddings(pickle.loads(blob))


def _scan_or_convert(
    connection: sqlite3.Connection,
    *,
    trusted_legacy: bool,
    update: bool,
) -> dict[str, int]:
    report = {
        "templates": 0,
        "already_safe": 0,
        "migrated": 0,
        "legacy_requires_trust": 0,
    }
    rows = connection.execute(
        "SELECT rowid, face_encoding FROM users "
        "WHERE face_encoding IS NOT NULL ORDER BY rowid"
    ).fetchall()
    for row in rows:
        report["templates"] += 1
        blob = bytes(row["face_encoding"])
        if blob.startswith(MAGIC):
            decode_embeddings(blob)
            report["already_safe"] += 1
            continue
        if not trusted_legacy:
            report["legacy_requires_trust"] += 1
            continue
        converted = _legacy_pickle_to_safe(blob)
        if update:
            connection.execute(
                "UPDATE users SET face_encoding=? WHERE rowid=?",
                (converted, row["rowid"]),
            )
        report["migrated"] += 1
    return report


def _current_user_sid() -> str:
    user_row = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return next(csv.reader([user_row]))[1]


def _restrict_to_current_user(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return

    sid = _current_user_sid()
    grant = f"*{sid}:{'(OI)(CI)' if path.is_dir() else ''}(F)"
    subprocess.run(
        [
            "icacls",
            str(path),
            "/inheritance:r",
            "/remove:g",
            "*S-1-5-18",
            "*S-1-5-32-544",
            "*S-1-3-4",
            "/grant:r",
            grant,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _private_windows_directory(path: Path) -> bool:
    script = (
        "$ErrorActionPreference='Stop';"
        "$acl=Get-Acl -LiteralPath $env:BASS_ACL_PATH;"
        "$rules=$acl.GetAccessRules($true,$true,"
        "[System.Security.Principal.SecurityIdentifier]);"
        "[pscustomobject]@{Protected=$acl.AreAccessRulesProtected;"
        "Sids=@($rules|ForEach-Object {$_.IdentityReference.Value})}"
        "|ConvertTo-Json -Compress"
    )
    environment = os.environ.copy()
    for key in list(environment):
        if key.casefold() == "psmodulepath":
            environment.pop(key)
    environment["BASS_ACL_PATH"] = str(path)
    raw = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    acl = json.loads(raw)
    sids = acl.get("Sids", [])
    if isinstance(sids, str):
        sids = [sids]
    return bool(acl.get("Protected")) and set(sids) == {_current_user_sid()}


def _prepare_private_output_parent(path: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise ValueError("output parent must be a directory")
    else:
        if not path.parent.is_dir():
            raise ValueError("only one private output directory may be created")
        path.mkdir(mode=0o700)
        _restrict_to_current_user(path)

    if os.name == "nt":
        private = _private_windows_directory(path)
    else:
        mode = stat.S_IMODE(path.stat().st_mode)
        private = path.stat().st_uid == os.geteuid() and mode & 0o077 == 0
    if not private:
        raise ValueError(
            "output parent must be a dedicated directory accessible only to the current user"
        )


def _new_private_temp(parent: Path, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        dir=parent,
        prefix=".bass-face-migration-",
        suffix=suffix,
    )
    os.close(descriptor)
    path = Path(raw_path)
    _restrict_to_current_user(path)
    return path


def _publish_without_overwrite(temporary: Path, destination: Path) -> None:
    os.link(temporary, destination)
    temporary.unlink()


def _dry_run(source: Path, trusted_legacy: bool) -> dict[str, object]:
    with closing(_open_read_only(source)) as connection:
        _require_schema(connection)
        result = _scan_or_convert(
            connection,
            trusted_legacy=trusted_legacy,
            update=False,
        )
    return {"mode": "dry-run", **result}


def _migrate(source: Path, output: Path, trusted_legacy: bool) -> dict[str, object]:
    output_temp = _new_private_temp(output.parent, ".db")
    manifest_temp = _new_private_temp(output.parent, ".json")
    manifest = output.with_name(f"{output.name}.manifest.json")
    try:
        source_fingerprint = _source_fingerprint(source)
        source_database = source_fingerprint["database"]
        if source_database is None:
            raise ValueError("source database disappeared before migration")
        source_file_sha256 = str(source_database["sha256"])
        with closing(_open_read_only(source)) as source_connection:
            _require_schema(source_connection)
            target_connection = sqlite3.connect(output_temp)
            try:
                source_connection.backup(target_connection)
            finally:
                target_connection.close()
        if _source_fingerprint(source) != source_fingerprint:
            raise ValueError("source database changed during the offline migration")

        source_snapshot_sha256 = _sha256(output_temp)
        target_connection = sqlite3.connect(output_temp)
        target_connection.row_factory = sqlite3.Row
        try:
            _require_schema(target_connection)
            user_triggers = _validated_user_triggers(target_connection)
            before_counts = _user_table_counts(target_connection)
            for name in user_triggers:
                quoted_name = name.replace('"', '""')
                target_connection.execute(f'DROP TRIGGER "{quoted_name}"')
            report = _scan_or_convert(
                target_connection,
                trusted_legacy=trusted_legacy,
                update=True,
            )
            if report["legacy_requires_trust"]:
                raise ValueError(
                    "legacy templates require the explicit --trusted-legacy acknowledgement"
                )
            for sql in user_triggers.values():
                target_connection.execute(sql)
            target_connection.commit()
            _require_schema(target_connection)
            integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError("migrated database failed SQLite integrity_check")
            if target_connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("migrated database failed foreign_key_check")
            if _user_table_counts(target_connection) != before_counts:
                raise ValueError("migration changed database row counts")
            remaining = _scan_or_convert(
                target_connection,
                trusted_legacy=False,
                update=False,
            )
            if remaining["legacy_requires_trust"]:
                raise ValueError("migration left legacy templates in the output")
        finally:
            target_connection.close()

        output_sha256 = _sha256(output_temp)
        manifest_data = {
            "schema": "bass-face-template-migration-v1",
            "source_file_sha256": source_file_sha256,
            "source_snapshot_sha256": source_snapshot_sha256,
            "output_sha256": output_sha256,
            **report,
        }
        manifest_temp.write_text(
            json.dumps(manifest_data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _restrict_to_current_user(manifest_temp)
        _publish_without_overwrite(output_temp, output)
        try:
            _publish_without_overwrite(manifest_temp, manifest)
        except Exception:
            output.unlink(missing_ok=True)
            raise
        return {"mode": "migrated", **report, "output_sha256": output_sha256}
    finally:
        output_temp.unlink(missing_ok=True)
        manifest_temp.unlink(missing_ok=True)


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    source = args.source_db.expanduser().resolve(strict=True)
    output = args.output_db.expanduser().resolve(strict=False)
    manifest = output.with_name(f"{output.name}.manifest.json")
    if not source.is_file():
        raise ValueError("source database must be a regular file")
    if source == output:
        raise ValueError("output database must be different from the source database")
    if output.exists() or manifest.exists():
        raise FileExistsError("output database or manifest already exists")
    return source, output, manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a verified SQLite database and convert legacy face templates. "
            "Never run this tool on an untrusted backup."
        )
    )
    parser.add_argument("source_db", type=Path)
    parser.add_argument("output_db", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--trusted-legacy",
        action="store_true",
        help=(
            "acknowledge that legacy templates came from a verified source; "
            "their conversion uses unsafe pickle deserialization"
        ),
    )
    args = parser.parse_args()

    try:
        source, output, _manifest = _paths(args)
        if args.dry_run:
            result = _dry_run(source, args.trusted_legacy)
        else:
            _prepare_private_output_parent(output.parent)
            result = _migrate(source, output, args.trusted_legacy)
    except (
        EmbeddingFormatError,
        OSError,
        sqlite3.Error,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
