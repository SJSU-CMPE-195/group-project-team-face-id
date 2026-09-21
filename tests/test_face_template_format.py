import hashlib
import importlib.util
import json
import pickle
import sqlite3
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import numpy as np

from car_face_auth.src import face_engine
from car_face_auth.src.embedding_format import (
    EMBEDDING_DIMENSION,
    HEADER,
    MAGIC,
    EmbeddingFormatError,
    TemplateMigrationRequired,
    decode_embeddings,
    encode_embeddings,
)
from wireless.security_schema import SCHEMA_STATEMENTS

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_SCRIPT = REPO_ROOT / "scripts" / "migrate-face-templates.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "bass_face_template_migration",
        MIGRATION_SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load migration module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_marker(path: str):
    Path(path).write_text("executed", encoding="utf-8")


class _MarkerPayload:
    def __init__(self, path: Path):
        self.path = path

    def __reduce__(self):
        return _write_marker, (str(self.path),)


def _sample(value: float = 1.0) -> np.ndarray:
    return np.full(EMBEDDING_DIMENSION, value, dtype=np.float32)


class _DbModule(types.ModuleType):
    def __init__(self, path: Path):
        super().__init__("db_api")
        self.path = path

    def get_conn(self):
        connection = sqlite3.connect(self.path, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def get_all_face_encodings(self):
        with self.get_conn() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT id, name, face_encoding FROM users "
                    "WHERE active=1 AND face_access=1 AND face_encoding IS NOT NULL"
                )
            ]

    def _insert_log(self, connection, stage, result, detail="", user_id=None):
        connection.execute(
            "INSERT INTO auth_logs (user_id, stage, result, detail) VALUES (?,?,?,?)",
            (user_id, stage, result, detail),
        )


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class FaceTemplateFormatTests(unittest.TestCase):
    def test_round_trip_has_exact_header_and_little_endian_payload(self):
        source = [_sample(1.25), _sample(-2.5)]
        blob = encode_embeddings(source)

        self.assertEqual(blob[: HEADER.size], struct.pack("<8sHH", MAGIC, 2, 512))
        self.assertEqual(len(blob), HEADER.size + 2 * 512 * 4)
        self.assertEqual(blob[HEADER.size : HEADER.size + 4], struct.pack("<f", 1.25))
        decoded = decode_embeddings(blob)
        self.assertEqual(len(decoded), 2)
        np.testing.assert_array_equal(decoded[0], source[0])
        np.testing.assert_array_equal(decoded[1], source[1])

    def test_strict_decoder_rejects_legacy_and_malformed_templates(self):
        with self.assertRaises(TemplateMigrationRequired):
            decode_embeddings(pickle.dumps([_sample()]))

        valid = encode_embeddings([_sample()])
        with self.assertRaisesRegex(EmbeddingFormatError, "length"):
            decode_embeddings(valid[:-1])
        with self.assertRaisesRegex(EmbeddingFormatError, "sample count"):
            decode_embeddings(struct.pack("<8sHH", MAGIC, 0, 512))
        with self.assertRaisesRegex(EmbeddingFormatError, "dimension"):
            decode_embeddings(struct.pack("<8sHH", MAGIC, 1, 511) + b"x" * 2044)

    def test_encoder_rejects_zero_nonfinite_and_oversized_collections(self):
        with self.assertRaisesRegex(EmbeddingFormatError, "all zero"):
            encode_embeddings([np.zeros(512, dtype=np.float32)])
        invalid = _sample()
        invalid[0] = np.nan
        with self.assertRaisesRegex(EmbeddingFormatError, "finite"):
            encode_embeddings([invalid])
        with self.assertRaisesRegex(EmbeddingFormatError, "at most 10"):
            encode_embeddings([_sample()] * 11)

    def test_runtime_never_executes_a_legacy_pickle_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            marker = Path(temp_dir) / "marker.txt"
            fake_db = types.ModuleType("db_api")
            fake_db.get_all_face_encodings = lambda: [
                {
                    "id": "user-1",
                    "name": "Ada",
                    "face_encoding": pickle.dumps(_MarkerPayload(marker)),
                }
            ]

            with mock.patch.dict(sys.modules, {"db_api": fake_db}):
                with self.assertLogs(face_engine.LOGGER, level="WARNING") as logs:
                    self.assertEqual(face_engine.load_database(), {})

            self.assertFalse(marker.exists())
            self.assertIn("offline migration is required", " ".join(logs.output))


class FaceTemplateDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "faceid.db"
        self.db_api = _DbModule(self.db_path)
        with self.db_api.get_conn() as connection:
            connection.executescript(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    face_encoding BLOB,
                    active INTEGER NOT NULL,
                    face_access INTEGER NOT NULL
                );
                CREATE TABLE auth_logs (
                    user_id TEXT,
                    stage TEXT,
                    result TEXT,
                    detail TEXT
                );
                INSERT INTO users VALUES ('user-1', 'Ada', X'6F6C64', 1, 1);
                """
            )
        self.module_patch = mock.patch.dict(sys.modules, {"db_api": self.db_api})
        self.module_patch.start()

    def tearDown(self):
        self.module_patch.stop()
        self.temp_dir.cleanup()

    def _stored_blob(self):
        with self.db_api.get_conn() as connection:
            return connection.execute(
                "SELECT face_encoding FROM users WHERE id='user-1'"
            ).fetchone()[0]

    def test_save_uses_safe_format_and_writes_audit_in_the_same_transaction(self):
        self.assertEqual(face_engine.save_user_embedding("Ada", [_sample()]), {"ok": True})
        self.assertTrue(self._stored_blob().startswith(MAGIC))
        with self.db_api.get_conn() as connection:
            log = connection.execute(
                "SELECT stage, result, detail FROM auth_logs"
            ).fetchone()
        self.assertEqual(dict(log), {
            "stage": "enroll_embedding",
            "result": "ok",
            "detail": "Face template stored",
        })

    def test_audit_failure_rolls_back_template_change(self):
        original = self._stored_blob()
        with mock.patch.object(
            self.db_api,
            "_insert_log",
            side_effect=sqlite3.OperationalError("injected audit failure"),
        ):
            result = face_engine.save_user_embedding("Ada", [_sample()])

        self.assertEqual(result, {"ok": False, "error": "database unavailable"})
        self.assertEqual(self._stored_blob(), original)

    def test_delete_reports_database_failures_and_does_not_claim_success(self):
        with mock.patch.object(
            self.db_api,
            "get_conn",
            side_effect=sqlite3.OperationalError("unavailable"),
        ):
            result = face_engine.delete_embedding_for_name("Ada")
        self.assertEqual(result, {"ok": False, "detail": "database unavailable"})


class FaceTemplateMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source.db"
        self.output = self.root / "private-output" / "output.db"
        self.legacy_blob = pickle.dumps([_sample(3.0)])
        with closing(sqlite3.connect(self.source)) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE users (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        face_encoding BLOB,
                        active INTEGER NOT NULL,
                        face_access INTEGER NOT NULL,
                        created_at INTEGER NOT NULL
                    );
                    CREATE TABLE auth_logs (id TEXT PRIMARY KEY, detail TEXT);
                    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO auth_logs VALUES ('log-1', 'preserve me');
                    INSERT INTO settings VALUES ('liveness_detection', 'true');
                    """
                )
                connection.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?)",
                    ("user-1", "Ada", self.legacy_blob, 1, 1, 123),
                )
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run(self, *extra: str):
        return subprocess.run(
            [
                sys.executable,
                str(MIGRATION_SCRIPT),
                str(self.source),
                str(self.output),
                *extra,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

    def test_dry_run_without_trust_never_creates_output(self):
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["legacy_requires_trust"], 1)
        self.assertEqual(report["migrated"], 0)
        self.assertFalse(self.output.exists())

    def test_trusted_migration_preserves_source_and_other_records(self):
        result = self._run("--trusted-legacy")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["migrated"], 1)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)

        with closing(sqlite3.connect(self.output)) as connection:
            blob = connection.execute(
                "SELECT face_encoding FROM users WHERE id='user-1'"
            ).fetchone()[0]
            self.assertEqual(connection.execute("SELECT detail FROM auth_logs").fetchone()[0], "preserve me")
            self.assertEqual(connection.execute("SELECT value FROM settings").fetchone()[0], "true")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        np.testing.assert_array_equal(decode_embeddings(blob)[0], _sample(3.0))

        manifest_path = self.output.with_name(f"{self.output.name}.manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "bass-face-template-migration-v1")
        self.assertEqual(
            manifest["output_sha256"],
            hashlib.sha256(self.output.read_bytes()).hexdigest(),
        )

    def test_wal_change_during_backup_aborts_without_publishing_output(self):
        migration = _load_migration_module()
        self.output.parent.mkdir(mode=0o700)
        real_open_read_only = migration._open_read_only

        class WalMutatingConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, *args, **kwargs):
                return self.connection.execute(*args, **kwargs)

            def backup(self, target):
                self.connection.backup(target)
                Path(f"{self_source}-wal").write_bytes(b"concurrent WAL write")

            def close(self):
                self.connection.close()

        self_source = self.source

        def open_and_mutate(path):
            return WalMutatingConnection(real_open_read_only(path))

        with mock.patch.object(
            migration,
            "_open_read_only",
            side_effect=open_and_mutate,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "source database changed",
            ):
                migration._migrate(self.source, self.output, trusted_legacy=True)

        self.assertFalse(self.output.exists())
        self.assertFalse(
            self.output.with_name(f"{self.output.name}.manifest.json").exists()
        )

    def test_known_security_triggers_are_preserved_without_side_effects(self):
        with closing(sqlite3.connect(self.source)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                for statement in SCHEMA_STATEMENTS:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO user_security "
                    "(user_id, pin_salt, pin_verifier, is_admin, failed_attempts, "
                    "lockout_until, auth_version, created_at, updated_at) "
                    "VALUES ('user-1', ?, ?, 1, 0, 0, 7, 123, 123)",
                    (b"s" * 16, b"v" * 32),
                )
                connection.execute(
                    "INSERT INTO paired_devices "
                    "(id, user_id, name, token_hash, kind, active, created_at, last_seen) "
                    "VALUES ('device-1', 'user-1', 'Phone', ?, 'mobile', 1, 123, 123)",
                    (b"d" * 32,),
                )
                connection.execute(
                    "INSERT INTO operation_grants "
                    "(token_hash, device_id, user_id, action, target, auth_version, "
                    "expires_at, created_at) VALUES (?, 'device-1', 'user-1', "
                    "'enrollment.start', 'user-1', 7, 9999999999, 123)",
                    (b"g" * 32,),
                )
            source_triggers = connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
            ).fetchall()

        result = self._run("--trusted-legacy")
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(sqlite3.connect(self.output)) as connection:
            connection.row_factory = sqlite3.Row
            auth_version = connection.execute(
                "SELECT auth_version FROM user_security WHERE user_id='user-1'"
            ).fetchone()[0]
            grant_count = connection.execute(
                "SELECT COUNT(*) FROM operation_grants"
            ).fetchone()[0]
            output_triggers = connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
            ).fetchall()

        self.assertEqual(auth_version, 7)
        self.assertEqual(grant_count, 1)
        self.assertEqual(
            [(row["name"], row["sql"]) for row in output_triggers],
            [(row["name"], row["sql"]) for row in source_triggers],
        )

    def test_unknown_user_trigger_is_rejected(self):
        with closing(sqlite3.connect(self.source)) as connection:
            with connection:
                connection.execute(
                    "CREATE TRIGGER unexpected_user_trigger "
                    "AFTER UPDATE OF face_encoding ON users "
                    "BEGIN UPDATE settings SET value='changed'; END"
                )

        result = self._run("--trusted-legacy")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported triggers on users", result.stderr)
        self.assertFalse(self.output.exists())

    def test_migration_without_trust_fails_without_publishing_output(self):
        result = self._run()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--trusted-legacy", result.stderr)
        self.assertFalse(self.output.exists())
        self.assertFalse(
            self.output.with_name(f"{self.output.name}.manifest.json").exists()
        )

    def test_safe_template_is_validated_without_being_reencoded(self):
        safe_blob = encode_embeddings([_sample(4.0)])
        with closing(sqlite3.connect(self.source)) as connection:
            with connection:
                connection.execute(
                    "UPDATE users SET face_encoding=? WHERE id='user-1'",
                    (safe_blob,),
                )

        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["already_safe"], 1)
        self.assertEqual(report["migrated"], 0)
        with closing(sqlite3.connect(self.output)) as connection:
            stored = connection.execute(
                "SELECT face_encoding FROM users WHERE id='user-1'"
            ).fetchone()[0]
        self.assertEqual(stored, safe_blob)

    def test_refuses_to_overwrite_an_existing_output(self):
        first = self._run("--trusted-legacy")
        self.assertEqual(first.returncode, 0, first.stderr)
        original = self.output.read_bytes()
        result = self._run("--trusted-legacy")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
