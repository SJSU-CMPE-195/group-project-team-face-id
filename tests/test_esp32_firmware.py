"""Compile and exercise the production ESP32 authentication state machine."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "esp32" / "firmware_harness.cpp"
INCLUDE_DIR = ROOT / "tests" / "esp32" / "include"


def compiler_command() -> list[str] | None:
    configured = os.environ.get("CXX")
    if configured:
        command = shlex.split(configured, posix=os.name != "nt")
        if not command:
            return None
        command[0] = command[0].strip('"')
        executable = Path(command[0]).stem.lower()
        if executable == "zig" and (len(command) == 1 or command[1] != "c++"):
            return [command[0], "c++", *command[1:]]
        return command

    for executable in ("g++", "clang++", "c++"):
        resolved = shutil.which(executable)
        if resolved:
            return [resolved]

    zig = shutil.which("zig")
    return [zig, "c++"] if zig else None


class Esp32FirmwareTests(unittest.TestCase):
    def test_production_authentication_state_machine(self) -> None:
        compiler = compiler_command()
        if compiler is None:
            self.skipTest(
                "C++17 compiler not found; set CXX to g++, clang++, or zig"
            )

        with tempfile.TemporaryDirectory(prefix="bass-esp32-firmware-") as folder:
            executable = Path(folder) / (
                "firmware_harness.exe" if os.name == "nt" else "firmware_harness"
            )
            compile_result = subprocess.run(
                [
                    *compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-pedantic",
                    "-I",
                    str(INCLUDE_DIR),
                    str(HARNESS),
                    "-o",
                    str(executable),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )

            run_result = subprocess.run(
                [str(executable)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stdout + run_result.stderr,
            )
            self.assertIn(
                "All 7 firmware state-machine tests passed",
                run_result.stdout,
            )


if __name__ == "__main__":
    unittest.main()
