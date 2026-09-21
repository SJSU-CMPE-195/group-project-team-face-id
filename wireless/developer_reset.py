"""Crash-aware developer reset with a coherent private backup first."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Iterable
import uuid

from .security import SecurityError


JOURNAL_VERSION = 1


def assert_reset_complete(
    config_path: Path, database_path: Path | None = None
) -> None:
    """Refuse ordinary startup while a developer reset needs recovery.

    ``database_path`` is accepted so startup call sites can make both protected
    paths explicit; the journal deliberately lives beside the device identity.
    """

    del database_path
    journal_path = (
        Path(config_path).resolve().parent / "developer-reset-journal.json"
    )
    if not journal_path.exists():
        return
    if journal_path.is_symlink():
        raise RuntimeError("developer reset journal must not be a symlink")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("developer reset journal is unreadable") from exc
    if (
        not isinstance(journal, dict)
        or journal.get("version") != JOURNAL_VERSION
    ):
        raise RuntimeError("developer reset journal is invalid")
    if journal.get("maintenance") or journal.get("active") is not None:
        raise RuntimeError(
            "A developer reset is incomplete. Restart on this PC with "
            "--hardware-simulator to recover it before normal operation."
        )


def make_private_directory(path: Path) -> None:
    """Create a directory and remove access inherited from other local users."""

    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError(f"private directory must not be a symlink: {path}")
    if os.name == "nt":
        _restrict_windows_path(path)
    else:
        os.chmod(path, 0o700)


def write_private_file(path: Path, content: bytes) -> None:
    """Replace a private file without a public-permission interval."""

    make_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        if os.name == "nt":
            _restrict_windows_path(temporary)
        else:
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if os.name == "nt":
            _restrict_windows_path(path)
        else:
            os.chmod(path, 0o600)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def protect_private_file(path: Path) -> None:
    """Validate and restrict an existing product database or identity file."""

    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"private file is unavailable: {path}")
    _make_private_file(path)


class DeveloperReset:
    """Keep reset idempotent across retries and fail closed across restarts."""

    def __init__(
        self,
        *,
        security,
        get_conn: Callable[[], sqlite3.Connection],
        state_dir: Path,
        backup_sources: Iterable[Path],
        reset_product: Callable[[sqlite3.Connection, str], Any],
        generation_provider: Callable[[], int | dict[str, Any]],
        events,
        quiesce_callback: Callable[[], None],
        reload_callback: Callable[[], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._security = security
        self._get_conn = get_conn
        self._state_dir = Path(state_dir).resolve()
        self._backup_sources = tuple(
            Path(path).resolve() for path in backup_sources
        )
        self._reset_product = reset_product
        self._generation_provider = generation_provider
        self._events = events
        self._quiesce_callback = quiesce_callback
        self._reload_callback = reload_callback
        self._clock = clock
        self._lock = threading.RLock()
        self._status_lock = threading.RLock()
        make_private_directory(self._state_dir)
        self._journal_path = self._state_dir / "developer-reset-journal.json"
        self._journal = self._load_journal()
        self._pending_snapshot = self._pending_from_journal(self._journal)
        self._ensure_receipt_schema()
        self._events.set_maintenance(bool(self._journal["maintenance"]))

    @property
    def maintenance(self) -> bool:
        with self._lock:
            return bool(self._journal["maintenance"])

    def pending_request(self) -> dict[str, str] | None:
        """Return durable recovery state without waiting for reset I/O."""

        with self._status_lock:
            if self._pending_snapshot is None:
                return None
            return dict(self._pending_snapshot)

    def execute(self, request_id: str, confirmation: str) -> dict[str, Any]:
        request_id = _canonical_uuid(request_id)
        if confirmation != "RESET":
            raise SecurityError(
                "reset_confirmation_required",
                400,
                "type RESET exactly to confirm the developer reset",
            )

        with self._events.lifecycle_lock, self._lock:
            existing = self._journal["receipts"].get(request_id)
            if existing is not None:
                return dict(existing)
            database_receipt = self._database_receipt(request_id)
            if database_receipt is not None:
                return self._recover_database_receipt(
                    request_id, database_receipt
                )
            active = self._journal.get("active")
            if active is not None and active.get("request_id") != request_id:
                raise SecurityError(
                    "maintenance",
                    503,
                    "a developer reset requires recovery before another reset",
                )
            if active is None:
                with self._security.synchronized():
                    active = {
                        "request_id": request_id,
                        "phase": "prepared",
                        "activation_secret": secrets.token_hex(32),
                        "before_generation": self._current_generation(),
                        "backup_directory": None,
                        "started_at": int(self._clock()),
                        "error": None,
                    }
                    self._journal["active"] = active
                    self._journal["maintenance"] = True
                    self._persist_journal()
                    self._events.set_maintenance(True)
            elif active.get("phase") == "failed_precommit":
                # The same request may safely retry because no durable database
                # receipt exists and the ownership generation is still checked.
                active["phase"] = "prepared"
                active["error"] = None
                self._persist_journal()

            self._events.set_maintenance(True)
            if active.get("phase") == "committed":
                return self._finish_committed(active)

            current_generation = self._current_generation()
            if current_generation > int(active["before_generation"]):
                active["phase"] = "committed"
                self._persist_journal()
                return self._finish_committed(active)
            if current_generation != int(active["before_generation"]):
                return self._fail_precommit(
                    active, "ownership generation changed unexpectedly"
                )

            try:
                self._quiesce_callback()
                with self._security.synchronized(), self._get_conn() as conn:
                    if self._current_generation() != int(
                        active["before_generation"]
                    ):
                        raise RuntimeError(
                            "ownership generation changed while entering "
                            "maintenance"
                        )
                    active["phase"] = "backing_up"
                    self._persist_journal()
                    backup_directory = self._create_backup(conn, request_id)
                    active["backup_directory"] = str(backup_directory)
                    active["receipt"] = {
                        "request_id": request_id,
                        "completed_at": int(self._clock()),
                        "backup_directory": str(backup_directory),
                        "generation": int(active["before_generation"]) + 1,
                    }
                    active["phase"] = "resetting"
                    self._persist_journal()
                    self._security._begin(conn)
                    try:
                        reset_result = self._reset_product(
                            conn, active["activation_secret"]
                        )
                        if isinstance(reset_result, dict) and isinstance(
                            reset_result.get("generation"), int
                        ):
                            active["receipt"]["generation"] = reset_result[
                                "generation"
                            ]
                        conn.execute(
                            "INSERT INTO developer_reset_receipts "
                            "(request_id, response_json, created_at) "
                            "VALUES (?,?,?)",
                            (
                                request_id,
                                json.dumps(
                                    active["receipt"],
                                    separators=(",", ":"),
                                    sort_keys=True,
                                ),
                                int(self._clock()),
                            ),
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                active["phase"] = "committed"
                self._persist_journal()
            except Exception as exc:
                database_receipt = self._database_receipt(request_id)
                if database_receipt is not None:
                    active["phase"] = "committed"
                    try:
                        self._persist_journal()
                    except Exception:
                        pass
                    return self._finish_committed(active)
                return self._fail_precommit(active, str(exc), cause=exc)

            return self._finish_committed(active)

    def _finish_committed(self, active: dict[str, Any]) -> dict[str, Any]:
        receipt = self._database_receipt(active["request_id"])
        if receipt is None:
            return self._fail_precommit(
                active,
                "product generation changed without a durable reset receipt",
            )
        try:
            self._reload_callback()
        except Exception as exc:
            active["error"] = f"reload failed: {exc}"
            active["phase"] = "committed"
            self._persist_journal()
            raise SecurityError(
                "reset_reload_failed",
                503,
                "product data was reset but runtime reload is still required",
            ) from exc

        request_id = active["request_id"]
        completed_journal = copy.deepcopy(self._journal)
        completed_journal["receipts"][request_id] = receipt
        completed_journal["active"] = None
        completed_journal["maintenance"] = False
        try:
            self._persist_journal(completed_journal)
        except Exception as exc:
            raise SecurityError(
                "reset_finalize_failed",
                503,
                "product data was reset but reset recovery is still pending",
            ) from exc
        self._events.close_windows()
        self._events.set_maintenance(False)
        return dict(receipt)

    def _recover_database_receipt(
        self, request_id: str, receipt: dict[str, Any]
    ) -> dict[str, Any]:
        active = self._journal.get("active")
        if active is not None and active.get("request_id") == request_id:
            self._events.set_maintenance(True)
            return self._finish_committed(active)
        self._journal["receipts"][request_id] = receipt
        self._persist_journal()
        return dict(receipt)

    def _fail_precommit(
        self,
        active: dict[str, Any],
        reason: str,
        *,
        cause: Exception | None = None,
    ) -> dict[str, Any]:
        active["phase"] = "failed_precommit"
        active["error"] = reason[:500]
        self._journal["maintenance"] = True
        self._persist_journal()
        error = SecurityError(
            "reset_failed",
            503,
            "developer reset failed before product data was cleared",
        )
        if cause is not None:
            raise error from cause
        raise error

    def _create_backup(
        self, source: sqlite3.Connection, request_id: str
    ) -> Path:
        backup_root = self._state_dir / "backups" / "developer-reset"
        make_private_directory(backup_root)
        backup_directory = backup_root / (
            f"{int(self._clock())}-{request_id}-{secrets.token_hex(4)}"
        )
        make_private_directory(backup_directory)
        database_path = backup_directory / "faceid.db"
        destination = sqlite3.connect(database_path)
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
        _make_private_file(database_path)

        database_entry = self._manifest_entry(database_path)
        database_entry["source"] = "product_database"
        copied: list[dict[str, Any]] = [database_entry]
        for source_path in self._backup_sources:
            if source_path.is_symlink() or not source_path.is_file():
                raise RuntimeError(
                    f"required backup source is unavailable: {source_path}"
                )
            destination_path = backup_directory / source_path.name
            if destination_path.exists():
                raise RuntimeError(
                    f"backup source names must be unique: {source_path.name}"
                )
            _copy_private_file(source_path, destination_path)
            entry = self._manifest_entry(destination_path)
            entry["source"] = str(source_path)
            copied.append(entry)

        manifest = {
            "version": 1,
            "request_id": request_id,
            "created_at": int(self._clock()),
            "files": copied,
        }
        write_private_file(
            backup_directory / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        return backup_directory

    @staticmethod
    def _manifest_entry(path: Path) -> dict[str, Any]:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "name": path.name,
            "sha256": digest.hexdigest(),
            "size": path.stat().st_size,
        }

    def _current_generation(self) -> int:
        value = self._generation_provider()
        if isinstance(value, dict):
            value = value.get("generation")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError("commissioning generation is unavailable")
        return value

    def _ensure_receipt_schema(self) -> None:
        with self._security.synchronized(), self._get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS developer_reset_receipts ("
                "request_id TEXT PRIMARY KEY, "
                "response_json TEXT NOT NULL, "
                "created_at INTEGER NOT NULL)"
            )

    def _database_receipt(self, request_id: str) -> dict[str, Any] | None:
        with self._security.synchronized(), self._get_conn() as conn:
            row = conn.execute(
                "SELECT response_json FROM developer_reset_receipts "
                "WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            receipt = json.loads(row["response_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "durable developer reset receipt is invalid"
            ) from exc
        if (
            not isinstance(receipt, dict)
            or receipt.get("request_id") != request_id
        ):
            raise RuntimeError("durable developer reset receipt is invalid")
        return receipt

    def _load_journal(self) -> dict[str, Any]:
        if not self._journal_path.exists():
            return {
                "version": JOURNAL_VERSION,
                "maintenance": False,
                "active": None,
                "receipts": {},
            }
        if self._journal_path.is_symlink():
            raise RuntimeError("developer reset journal must not be a symlink")
        try:
            journal = json.loads(self._journal_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("developer reset journal is unreadable") from exc
        if (
            not isinstance(journal, dict)
            or journal.get("version") != JOURNAL_VERSION
            or not isinstance(journal.get("maintenance"), bool)
            or not isinstance(journal.get("receipts"), dict)
            or journal.get("active") is not None
            and not isinstance(journal.get("active"), dict)
        ):
            raise RuntimeError("developer reset journal is invalid")
        return journal

    def _persist_journal(
        self, replacement: dict[str, Any] | None = None
    ) -> None:
        journal = replacement if replacement is not None else self._journal
        write_private_file(
            self._journal_path,
            (json.dumps(journal, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        if replacement is not None:
            self._journal = replacement
        pending = self._pending_from_journal(journal)
        with self._status_lock:
            self._pending_snapshot = pending

    @staticmethod
    def _pending_from_journal(
        journal: dict[str, Any],
    ) -> dict[str, str] | None:
        active = journal.get("active")
        if not isinstance(active, dict):
            return None
        request_id = active.get("request_id")
        phase = active.get("phase")
        if not isinstance(request_id, str) or not isinstance(phase, str):
            return None
        return {"request_id": request_id, "phase": phase}


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value) if isinstance(value, str) else None
    except ValueError as exc:
        raise SecurityError(
            "invalid_reset_request", 400, "request_id must be a UUID"
        ) from exc
    if parsed is None or str(parsed) != value:
        raise SecurityError(
            "invalid_reset_request", 400, "request_id must be a UUID"
        )
    return value


def _copy_private_file(source: Path, destination: Path) -> None:
    with source.open("rb") as input_file:
        write_private_file(destination, input_file.read())


def _make_private_file(path: Path) -> None:
    if os.name == "nt":
        _restrict_windows_path(path)
    else:
        os.chmod(path, 0o600)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise RuntimeError(
                f"backup file permissions must be private: {path}"
            )


def _restrict_windows_path(path: Path) -> None:
    identity = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sid = next(csv.reader([identity.stdout.strip()]))[1]
    permission = f"*{sid}:(OI)(CI)(F)" if path.is_dir() else f"*{sid}:(F)"
    subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", permission],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
