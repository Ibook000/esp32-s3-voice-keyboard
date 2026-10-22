"""验证 EC11 解码、开关消抖、断线和键盘报告合并，不向电脑发送输入。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class EncoderFirmwareTests(unittest.TestCase):
    def test_encoder_firmware_on_simulated_gpio(self):
        compiler = shutil.which("g++")
        if compiler is None:
            self.skipTest("g++ unavailable for encoder firmware test")
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / "encoder.exe"
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
                    str(root / "src/rotary_encoder.cpp"),
                    str(root / "pc/tests/rotary_fixture.cpp"),
                    "-o",
                    str(executable),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("11 encoder scenarios passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
