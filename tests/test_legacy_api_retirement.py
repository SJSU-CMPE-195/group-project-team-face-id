"""Regression checks for retired unauthenticated API entrypoints."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARDWARE_MODULES = (
    "cv2",
    "insightface",
    "numpy",
    "picamera2",
    "serial",
)


def hardware_import_guard() -> str:
    blocked = repr(HARDWARE_MODULES)
    return f"""
import importlib.abc
import sys

class RejectHardwareImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.', 1)[0] in {blocked}:
            raise AssertionError(f'blocked hardware import: {{fullname}}')
        return None

sys.meta_path.insert(0, RejectHardwareImports())
"""


def bash_executable() -> str | None:
    if os.name == "nt":
        git_bash = Path(os.environ.get("PROGRAMFILES", "")) / "Git/bin/bash.exe"
        if git_bash.is_file():
            return str(git_bash)
    return shutil.which("bash")


class LegacyApiRetirementTests(unittest.TestCase):
    def test_canonical_flask_factory_remains_available(self):
        from pi_device_api import create_app

        self.assertTrue(callable(create_app))

    def test_fastapi_module_fails_closed(self):
        result = subprocess.run(
            [sys.executable, "-c", "import car_face_auth.src.api_server"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("standalone Face API has been retired", result.stderr)
        self.assertIn("bass_wireless.py", result.stderr)

    def test_retired_hardware_clis_import_without_loading_hardware(self):
        guard = hardware_import_guard()
        for module_name in (
            "car_face_auth.src.enroll",
            "car_face_auth.src.verify_live",
            "car_face_auth.src.test_insightface",
        ):
            with self.subTest(module=module_name):
                result = subprocess.run(
                    [sys.executable, "-c", guard + f"\nimport {module_name}"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("blocked hardware import", result.stderr)

    def test_retired_hardware_clis_exit_nonzero_without_loading_hardware(self):
        guard = hardware_import_guard()
        for module_name, expected_text in (
            ("car_face_auth.src.enroll", "standalone enrollment CLI has been retired"),
            ("car_face_auth.src.test_insightface", "standalone enrollment debugger has been retired"),
            (
                "car_face_auth.src.verify_live",
                "standalone live-verification CLI has been retired",
            ),
        ):
            with self.subTest(module=module_name):
                script = (
                    guard
                    + "\nimport runpy"
                    + f"\nrunpy.run_module({module_name!r}, run_name='__main__')"
                )
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(expected_text, result.stderr)
                self.assertIn("bass_wireless.py", result.stderr)
                self.assertNotIn("blocked hardware import", result.stderr)

    @unittest.skipUnless(bash_executable(), "bash is unavailable")
    def test_legacy_installer_fails_without_side_effects(self):
        result = subprocess.run(
            [bash_executable(), "install.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("standalone FaceID service has been retired", result.stderr)
        self.assertIn("scripts/install-wireless-pi.sh", result.stderr)

    def test_legacy_systemd_unit_is_removed(self):
        self.assertFalse((ROOT / "systemd/faceid-api.service").exists())


if __name__ == "__main__":
    unittest.main()
