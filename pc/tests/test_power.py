from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class PowerTests(unittest.TestCase):
    def test_real_power_manager_with_simulated_sleep_and_gpio(self):
        compiler = shutil.which("g++")
        if not compiler:
            self.skipTest("g++ unavailable")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            exe = Path(folder) / "power.exe"
            args = [
                compiler,
                "-std=c++11",
                "-static",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(root / "pc/tests/power_fakes"),
                "-I",
                str(root / "include"),
                str(root / "src/keymap.cpp"),
                str(root / "src/power_manager.cpp"),
                str(root / "pc/tests/power_fixture.cpp"),
                "-o",
                str(exe),
            ]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("10 deep sleep scenarios passed", result.stdout)
