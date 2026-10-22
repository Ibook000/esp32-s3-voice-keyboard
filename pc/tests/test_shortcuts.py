"""在模拟 GPIO/BLE 环境中执行实际的 C++ 快捷键固件，不发送真实电脑按键。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class ShortcutFirmwareTests(unittest.TestCase):
    def test_shortcut_firmware_on_simulated_gpio(self):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("g++ unavailable for shortcut firmware test")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "shortcuts.exe"
            build = subprocess.run(
                [
                    compiler,
                    "-std=c++11",
                    "-static",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root / "pc/tests/fakes"),
                    "-I",
                    str(root / "include"),
                    str(root / "src/keymap.cpp"),
                    str(root / "src/shortcut_keys.cpp"),
                    str(root / "pc/tests/shortcut_fixture.cpp"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("9 shortcut scenarios passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
