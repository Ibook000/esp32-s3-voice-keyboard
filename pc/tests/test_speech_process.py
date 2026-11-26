from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stream_asr
from speech_process import ProcessSpeechOutput, StreamSpeechOutput

STREAM_STUB = """
import json, sys
P = "IBOOK_ASR "
out = sys.stdout
def send(value):
    out.write(P + json.dumps(value, ensure_ascii=False) + "\\n")
    out.flush()
send({"ready": True})
for line in sys.stdin:
    message = json.loads(line)
    if message.get("cmd") == "pcm":
        send({"partial": "识别中"})
    elif message.get("cmd") == "end":
        send({"commit": "你好。"})
        send({"done": {"text": "你好。", "tail": "你好。"}})
"""


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

    def test_stream_engine_round_trip_delivers_commits_and_text(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = StreamSpeechOutput(
                argparse.Namespace(transcribe=True, state_dir=folder, model="stub")
            )
            self.addCleanup(engine.close)
            self.assertTrue(engine.streaming)
            commits, partials = [], []
            engine.attach(commits.append, partials.append)
            engine.spawn(
                [sys.executable, "-X", "utf8", "-c", STREAM_STUB], Path(folder) / "stub.log"
            )
            engine.begin()
            engine.push(bytes(640))
            self.assertEqual(engine.end(), "你好。")
            self.assertEqual(commits, ["你好。"])
            self.assertEqual(partials[-1], "")
            engine.close()

    def test_stream_engine_is_inert_without_transcription(self):
        engine = StreamSpeechOutput(argparse.Namespace(transcribe=False, state_dir="."))
        self.assertFalse(engine.streaming)
        self.assertIsNone(engine.process(Path("x.wav"), False))


class StreamModelTests(unittest.TestCase):
    def test_official_14m_model_prefers_int8_and_falls_back_to_fp32(self):
        name = "zipformer-zh-14m"
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            stream_asr, "MODEL_ROOT", Path(folder)
        ):
            model = stream_asr.model_dir(name)
            model.mkdir()
            decoder = model / "decoder-epoch-99-avg-1.onnx"
            tokens = model / "tokens.txt"
            paths_by_precision = {}
            for precision in ("fp32", "int8"):
                suffix = ".int8.onnx" if precision == "int8" else ".onnx"
                encoder = model / f"encoder-epoch-99-avg-1{suffix}"
                joiner = model / f"joiner-epoch-99-avg-1{suffix}"
                paths = (encoder, decoder, joiner, tokens)
                for path in paths:
                    path.touch()
                paths_by_precision[precision] = paths
                self.assertEqual(stream_asr.model_files(name), paths)

            paths_by_precision["int8"][2].unlink()
            self.assertEqual(stream_asr.model_files(name), paths_by_precision["fp32"])
