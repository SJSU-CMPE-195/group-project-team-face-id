"""Isolation and browser-boundary checks for the standalone mock API."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _HealthRuntime:
    def status(self):
        return {
            "ready": False,
            "camera_open": False,
            "esp32_connected": False,
            "ignitionOn": False,
        }


class MockDatabaseIsolationTests(unittest.TestCase):
    def test_inherited_database_path_is_ignored_by_standalone_mock(self):
        with tempfile.TemporaryDirectory(prefix="bass-mock-isolation-") as tempdir:
            temp_root = Path(tempdir)
            production_path = temp_root / "production.db"
            mock_path = temp_root / "mock.db"
            conn = sqlite3.connect(production_path)
            try:
                conn.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
                conn.execute("INSERT INTO sentinel VALUES ('do-not-touch')")
                conn.commit()
            finally:
                conn.close()
            before = _sha256(production_path)

            script = textwrap.dedent(
                f"""
                import os
                from pathlib import Path

                production_path = Path({str(production_path)!r})
                mock_path = Path({str(mock_path)!r})
                os.environ["FACEID_DB_PATH"] = str(production_path)

                import mock_pi_device_api

                assert os.environ["FACEID_DB_PATH"] == str(production_path)
                db_module, db_api_module = (
                    mock_pi_device_api._load_standalone_database(mock_path)
                )
                assert Path(db_module.DB_PATH).resolve() == mock_path.resolve()
                db_module.init_db()
                result = db_api_module.add_user("Mock Only")
                assert "error" not in result, result
                assert mock_path.is_file()
                """
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", script],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(_sha256(production_path), before)
            conn = sqlite3.connect(production_path)
            try:
                self.assertEqual(
                    conn.execute("SELECT value FROM sentinel").fetchall(),
                    [("do-not-touch",)],
                )
            finally:
                conn.close()

    def test_cached_production_database_cannot_be_repointed(self):
        with tempfile.TemporaryDirectory(prefix="bass-mock-cached-") as tempdir:
            temp_root = Path(tempdir)
            production_path = temp_root / "production.db"
            mock_path = temp_root / "mock.db"
            production_path.write_bytes(b"sentinel")
            before = _sha256(production_path)
            script = textwrap.dedent(
                f"""
                import os
                from pathlib import Path

                production_path = Path({str(production_path)!r})
                mock_path = Path({str(mock_path)!r})
                os.environ["FACEID_DB_PATH"] = str(production_path)
                import db
                import mock_pi_device_api

                try:
                    mock_pi_device_api._load_standalone_database(mock_path)
                except RuntimeError as exc:
                    assert "refusing to repoint" in str(exc), exc
                else:
                    raise AssertionError("cached production database was repointed")
                assert Path(db.DB_PATH).resolve() == production_path.resolve()
                assert os.environ["FACEID_DB_PATH"] == str(production_path)
                assert not mock_path.exists()
                """
            )
            result = subprocess.run(
                [sys.executable, "-B", "-c", script],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(_sha256(production_path), before)


class MockBrowserBoundaryTests(unittest.TestCase):
    def setUp(self):
        from mock_pi_device_api import create_mock_app

        self.app = create_mock_app(object(), runtime=_HealthRuntime())
        self.app.testing = True
        self.client = self.app.test_client()

    def get_health(self, *, base_url="http://localhost:5055", headers=None):
        return self.client.get(
            "/health",
            base_url=base_url,
            headers=headers or {},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

    def test_empty_and_loopback_browser_origins_are_allowed(self):
        self.assertEqual(self.get_health().status_code, 200)
        self.assertEqual(
            self.get_health(headers={"Origin": "http://localhost:5173"}).status_code,
            200,
        )
        self.assertEqual(
            self.get_health(
                headers={"Referer": "http://127.0.0.1:5173/dashboard"}
            ).status_code,
            200,
        )

    def test_external_host_and_browser_origins_are_rejected(self):
        external_host = self.get_health(base_url="http://outside.example:5055")
        self.assertEqual(external_host.status_code, 403)
        self.assertIn("Host", external_host.get_json()["error"])

        for header in ("Origin", "Referer"):
            with self.subTest(header=header):
                response = self.get_health(
                    headers={header: "https://outside.example/attack"}
                )
                self.assertEqual(response.status_code, 403)
                self.assertIn("origin", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
