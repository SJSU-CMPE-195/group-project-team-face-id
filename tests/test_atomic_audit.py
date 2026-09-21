import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import db
import db_api


class AtomicAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = mock.patch.object(
            db,
            "DB_PATH",
            str(Path(self.temp_dir.name) / "faceid.db"),
        )
        self.db_path_patch.start()
        db.init_db()

    def tearDown(self):
        self.db_path_patch.stop()
        self.temp_dir.cleanup()

    def _seed_user(self, *, embedding=b"original"):
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO users "
                "(id, name, face_encoding, active, face_access, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("user-1", "Ada", embedding, 1, 1, 1),
            )

    def _row(self, sql, params=()):
        with db.get_conn() as conn:
            return conn.execute(sql, params).fetchone()

    def _fail_after_log_is_inserted(self):
        original_insert_log = db_api._insert_log

        def insert_then_fail(conn, *args, **kwargs):
            original_insert_log(conn, *args, **kwargs)
            raise sqlite3.OperationalError("injected audit failure")

        return mock.patch.object(
            db_api,
            "_insert_log",
            side_effect=insert_then_fail,
        )

    def _assert_no_audit_rows(self):
        row = self._row("SELECT COUNT(*) AS count FROM auth_logs")
        self.assertEqual(row["count"], 0)

    def test_add_user_rolls_back_user_and_audit_when_audit_fails(self):
        with self._fail_after_log_is_inserted():
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected audit failure",
            ):
                db_api.add_user(" New Driver ", b"embedding")

        row = self._row("SELECT COUNT(*) AS count FROM users")
        self.assertEqual(row["count"], 0)
        self._assert_no_audit_rows()

    def test_delete_user_rolls_back_user_and_audit_when_audit_fails(self):
        self._seed_user()

        with self._fail_after_log_is_inserted():
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected audit failure",
            ):
                db_api.delete_user("user-1")

        row = self._row(
            "SELECT active, face_access, face_encoding FROM users WHERE id=?",
            ("user-1",),
        )
        self.assertEqual(dict(row), {
            "active": 1,
            "face_access": 1,
            "face_encoding": b"original",
        })
        self._assert_no_audit_rows()

    def test_access_change_rolls_back_access_and_audit_when_audit_fails(self):
        self._seed_user()

        with self._fail_after_log_is_inserted():
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected audit failure",
            ):
                db_api.set_user_access("user-1", False)

        row = self._row("SELECT face_access FROM users WHERE id=?", ("user-1",))
        self.assertEqual(row["face_access"], 1)
        self._assert_no_audit_rows()

    def test_embedding_change_rolls_back_embedding_and_audit_when_audit_fails(self):
        self._seed_user()

        with self._fail_after_log_is_inserted():
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected audit failure",
            ):
                db_api.set_user_embedding("user-1", b"replacement")

        row = self._row("SELECT face_encoding FROM users WHERE id=?", ("user-1",))
        self.assertEqual(row["face_encoding"], b"original")
        self._assert_no_audit_rows()

    def test_settings_roll_back_values_and_audit_when_audit_fails(self):
        with self._fail_after_log_is_inserted():
            with self.assertRaisesRegex(
                sqlite3.OperationalError,
                "injected audit failure",
            ):
                db_api.save_settings_from_ui(
                    {
                        "autoRelockSeconds": 45,
                        "liveness": False,
                    }
                )

        settings = db_api.get_settings()
        self.assertEqual(settings["auto_relock_seconds"], "10")
        self.assertEqual(settings["liveness_detection"], "true")
        self._assert_no_audit_rows()

    def test_failed_mutation_does_not_create_audit_row(self):
        self._seed_user()
        with db.get_conn() as conn:
            conn.execute(
                "CREATE TRIGGER reject_access_change "
                "BEFORE UPDATE OF face_access ON users "
                "BEGIN SELECT RAISE(ABORT, 'injected mutation failure'); END"
            )

        with mock.patch.object(
            db_api,
            "_insert_log",
            wraps=db_api._insert_log,
        ) as insert_log:
            with self.assertRaisesRegex(
                sqlite3.IntegrityError,
                "injected mutation failure",
            ):
                db_api.set_user_access("user-1", False)

        insert_log.assert_not_called()
        row = self._row("SELECT face_access FROM users WHERE id=?", ("user-1",))
        self.assertEqual(row["face_access"], 1)
        self._assert_no_audit_rows()

    def test_successful_mutations_keep_existing_results_and_write_audit(self):
        user = db_api.add_user(" Ada ", b"first")
        self.assertEqual(user["name"], "Ada")
        self.assertEqual(db_api.set_user_embedding(user["id"], b"second"), {"ok": True})
        self.assertEqual(db_api.set_user_access(user["id"], False), {"ok": True})
        self.assertEqual(
            db_api.save_settings_from_ui({"autoRelockSeconds": 30}),
            {"ok": True},
        )
        self.assertEqual(db_api.delete_user(user["id"]), {"ok": True})

        with db.get_conn() as conn:
            stages = [
                row["stage"]
                for row in conn.execute("SELECT stage FROM auth_logs ORDER BY rowid")
            ]
        self.assertEqual(
            stages,
            [
                "enroll",
                "enroll_embedding",
                "access_change",
                "settings",
                "delete_user",
            ],
        )


if __name__ == "__main__":
    unittest.main()
