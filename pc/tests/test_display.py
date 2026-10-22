"""验证 OLED 显示状态和格式，不依赖屏幕硬件或 Arduino 图形库。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class DisplayModelTests(unittest.TestCase):
    def test_display_model_with_device_snapshots(self):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("g++ unavailable for display model test")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "display.exe"
            build = subprocess.run(
                [
                    compiler,
                    "-std=c++11",
                    "-static",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(root / "include"),
                    str(root / "src/keymap.cpp"),
                    str(root / "src/display_model.cpp"),
                    str(root / "pc/tests/display_fixture.cpp"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("8 display scenarios passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
