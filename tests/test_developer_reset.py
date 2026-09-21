"""Developer reset backup, failure, and retry safety on temporary data only."""

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
import uuid

from wireless import developer_reset as developer_reset_module
from wireless.developer_reset import DeveloperReset, assert_reset_complete
from wireless.security import SecurityError


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class _Security:
    def __init__(self):
        self.lock = threading.RLock()

    @contextmanager
    def synchronized(self):
        with self.lock:
            yield

    @staticmethod
    def _begin(conn):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")


class _Events:
    def __init__(self):
        self.lifecycle_lock = threading.RLock()
        self.maintenance = False
        self.closed = 0

    def set_maintenance(self, enabled):
        self.maintenance = enabled

    def close_windows(self):
        self.closed += 1


class DeveloperResetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.state = self.root / "state"
        self.state.mkdir()
        self.db_path = self.root / "faceid.db"
        self.config = self.state / "device.json"
        self.tls = self.state / "device.tls.pem"
        self.secret = self.state / "device.auth.key"
        self.config.write_text('{"device_id":"test"}', encoding="utf-8")
        self.tls.write_text("tls", encoding="utf-8")
        self.secret.write_bytes(b"s" * 32)
        with self.connection() as conn:
            conn.executescript(
                "CREATE TABLE users(id TEXT PRIMARY KEY);"
                "CREATE TABLE commissioning_state("
                "id INTEGER PRIMARY KEY, generation INTEGER NOT NULL, "
                "activation_secret TEXT);"
                "INSERT INTO users VALUES('existing');"
                "INSERT INTO commissioning_state VALUES(1,7,'old');"
            )
        self.security = _Security()
        self.events = _Events()
        self.quiesce_calls = 0
        self.reload_calls = 0
        self.reload_failure = False

    def connection(self):
        connection = sqlite3.connect(self.db_path, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def generation(self):
        with self.connection() as conn:
            return conn.execute(
                "SELECT generation FROM commissioning_state WHERE id=1"
            ).fetchone()["generation"]

    @staticmethod
    def reset_product(conn, activation_secret):
        conn.execute("DELETE FROM users")
        conn.execute(
            "UPDATE commissioning_state SET generation=generation+1, "
            "activation_secret=? WHERE id=1",
            (activation_secret,),
        )
        generation = conn.execute(
            "SELECT generation FROM commissioning_state WHERE id=1"
        ).fetchone()["generation"]
        return {"generation": generation}

    def quiesce(self):
        self.quiesce_calls += 1

    def reload(self):
        self.reload_calls += 1
        if self.reload_failure:
            raise RuntimeError("reload unavailable")

    def controller(self, *, sources=None):
        return DeveloperReset(
            security=self.security,
            get_conn=self.connection,
            state_dir=self.state,
            backup_sources=sources or (self.config, self.tls, self.secret),
            reset_product=self.reset_product,
            generation_provider=self.generation,
            events=self.events,
            quiesce_callback=self.quiesce,
            reload_callback=self.reload,
            clock=lambda: 1234.0,
        )

    def test_backup_then_atomic_reset_and_idempotent_receipt(self):
        controller = self.controller()
        request_id = str(uuid.uuid4())
        receipt = controller.execute(request_id, "RESET")
        self.assertEqual(receipt["generation"], 8)
        backup = Path(receipt["backup_directory"])
        self.assertTrue((backup / "manifest.json").is_file())
        manifest = json.loads(
            (backup / "manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["name"] for item in manifest["files"]},
            {"faceid.db", "device.json", "device.tls.pem", "device.auth.key"},
        )
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 0)
            conn.execute("INSERT INTO users VALUES('new-after-reset')")
        repeated = controller.execute(request_id, "RESET")
        self.assertEqual(repeated, receipt)
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 1)
        self.assertEqual(self.quiesce_calls, 1)
        self.assertFalse(controller.maintenance)
        self.assertIsNone(controller.pending_request())

    def test_pending_request_does_not_wait_for_reset_lock(self):
        controller = self.controller()
        completed = threading.Event()
        result = []

        def read_pending():
            result.append(controller.pending_request())
            completed.set()

        controller._lock.acquire()
        try:
            reader = threading.Thread(target=read_pending)
            reader.start()
            self.assertTrue(completed.wait(0.5))
        finally:
            controller._lock.release()
        reader.join(1)
        self.assertEqual(result, [None])

    def test_backup_failure_clears_nothing_and_same_request_can_retry(self):
        missing = self.state / "missing.pem"
        controller = self.controller(sources=(self.config, missing))
        request_id = str(uuid.uuid4())
        with self.assertRaises(SecurityError) as failed:
            controller.execute(request_id, "RESET")
        self.assertEqual(failed.exception.code, "reset_failed")
        self.assertEqual(
            controller.pending_request(),
            {"request_id": request_id, "phase": "failed_precommit"},
        )
        self.assertTrue(controller.maintenance)
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 1)
        missing.write_text("now available", encoding="utf-8")
        receipt = controller.execute(request_id, "RESET")
        self.assertEqual(receipt["generation"], 8)

    def test_committed_receipt_prevents_second_wipe_after_reload_failure(self):
        self.reload_failure = True
        controller = self.controller()
        request_id = str(uuid.uuid4())
        with self.assertRaises(SecurityError) as failed:
            controller.execute(request_id, "RESET")
        self.assertEqual(failed.exception.code, "reset_reload_failed")
        with self.connection() as conn:
            conn.execute("INSERT INTO users VALUES('must-survive-retry')")
        self.reload_failure = False
        receipt = controller.execute(request_id, "RESET")
        self.assertEqual(receipt["generation"], 8)
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 1)

    def test_final_journal_failure_retries_without_second_wipe(self):
        controller = self.controller()
        request_id = str(uuid.uuid4())
        real_write = developer_reset_module.write_private_file
        failed_once = False

        def fail_final_write(path, content):
            nonlocal failed_once
            if (
                path.name == "developer-reset-journal.json"
                and b'"maintenance": false' in content
                and not failed_once
            ):
                failed_once = True
                raise OSError("simulated final journal failure")
            return real_write(path, content)

        with mock.patch.object(
            developer_reset_module,
            "write_private_file",
            side_effect=fail_final_write,
        ):
            with self.assertRaises(SecurityError) as failed:
                controller.execute(request_id, "RESET")
        self.assertEqual(failed.exception.code, "reset_finalize_failed")
        self.assertTrue(controller.maintenance)
        self.assertEqual(
            controller.pending_request(),
            {"request_id": request_id, "phase": "committed"},
        )
        with self.connection() as conn:
            conn.execute("INSERT INTO users VALUES('after-committed-reset')")

        receipt = controller.execute(request_id, "RESET")
        self.assertEqual(receipt["request_id"], request_id)
        self.assertFalse(controller.maintenance)
        self.assertIsNone(controller.pending_request())
        with self.connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            self.assertEqual(count, 1)

    def test_ordinary_startup_refuses_pending_maintenance(self):
        controller = self.controller(sources=(self.state / "missing",))
        with self.assertRaises(SecurityError):
            controller.execute(str(uuid.uuid4()), "RESET")
        with self.assertRaisesRegex(RuntimeError, "--hardware-simulator"):
            assert_reset_complete(self.config, self.db_path)


if __name__ == "__main__":
    unittest.main()
