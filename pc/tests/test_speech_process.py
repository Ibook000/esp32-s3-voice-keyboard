from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from speech_process import ProcessSpeechOutput


class SpeechProcessTests(unittest.TestCase):
    def engine(self, source):
        engine = ProcessSpeechOutput(argparse.Namespace(transcribe=True))
        engine.process_handle = subprocess.Popen(
            [sys.executable, "-c", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self.addCleanup(engine.close)
        return engine

    def test_native_exit_returns_error_to_parent_without_terminating_it(self):
        engine = self.engine("import os;os._exit(23)")
        with self.assertRaisesRegex(RuntimeError, "0x00000017"):
            engine.read()
        self.assertEqual(2 + 2, 4)

    def test_stop_terminates_worker_while_loading(self):
        engine = self.engine("import time;time.sleep(60)")
        engine.close()
        self.assertIsNotNone(engine.process_handle.poll())
        self.assertTrue(engine.closed)
        engine.close()

    def test_worker_error_and_unicode_response(self):
        engine = self.engine(
            "import json;print('IBOOK_ASR '+json.dumps({'error':'model failed'}),flush=True)"
        )
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            engine.read()
        engine = self.engine(
            "import json;print('IBOOK_ASR '+json.dumps({'text':chr(20320)+chr(22909)}),flush=True)"
        )
        self.assertEqual(engine.read()["text"], "你好")
