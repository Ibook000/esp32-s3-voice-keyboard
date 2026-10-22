"""本地语音引擎的受管理子进程，原生扩展崩溃不会带走 BLE/Web 主进程。

仅传递已保存的 WAV 路径和识别文字，不传送到外部服务。
禁用语音时主动结束子进程，也可中止正在下载或加载的模型。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

PREFIX = "IBOOK_ASR "


class ProcessSpeechOutput:
    def __init__(self, args):
        self.args = args
        self.process_handle = None
        self.closed = False
        self.lock = threading.Lock()

    def read(self):
        process = self.process_handle
        while True:
            line = process.stdout.readline()
            if not line:
                code = process.wait(timeout=5)
                raise RuntimeError(
                    f"语音引擎已退出（0x{code & 0xFFFFFFFF:08X}），工作台仍可使用。"
                    "请重新应用语音设置；详情见 runtime/asr-worker.log。"
                )
            if line.startswith(PREFIX):
                result = json.loads(line[len(PREFIX) :])
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result

    def prepare(self):
        if not self.args.transcribe:
            return
        if self.closed:
            raise RuntimeError("语音已停止。")
        folder = Path(self.args.state_dir)
        folder.mkdir(parents=True, exist_ok=True)
        executable = Path(sys.executable)
        if executable.name.casefold() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        command = [
            str(executable),
            "-X",
            "utf8",
            "-X",
            "faulthandler",
            __file__,
            "--worker",
            "--model",
            self.args.model,
            "--language",
            self.args.language,
        ]
        environment = os.environ.copy()
        # 使用标准 HTTP 下载，减少模型下载阶段的原生组件依赖。
        environment["HF_HUB_DISABLE_XET"] = "1"
        with (folder / "asr-worker.log").open("ab") as log:
            self.process_handle = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        if self.closed:
            self.close()
            raise RuntimeError("语音已停止。")
        try:
            if not self.read().get("ready"):
                raise RuntimeError("语音引擎未能完成初始化。")
        except BaseException:
            self.close()
            raise

    def process(self, path, test_tone):
        if not self.args.transcribe or test_tone:
            return None
        with self.lock:
            if self.closed:
                raise RuntimeError("语音已停止。")
            process = self.process_handle
            try:
                process.stdin.write(json.dumps({"path": str(path.resolve())}) + "\n")
                process.stdin.flush()
                return self.read()["text"]
            except (OSError, ValueError) as exc:
                raise RuntimeError("语音引擎连接已关闭，请重新应用语音设置。") from exc

    def close(self):
        self.closed = True
        process = self.process_handle
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                with contextlib.suppress(OSError, ValueError):
                    stream.close()


def worker():
    # 必须在导入 CTranslate2/ONNX 等扩展前设置；故障回传父进程，不弹系统对话框。
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetErrorMode(0x8003)
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--model", default="tiny")
    parser.add_argument("--language", default="zh")
    args = parser.parse_args()
    args.transcribe, args.type_text = True, False
    protocol = sys.stdout
    sys.stdout = sys.stderr  # 模型的普通输出进入日志，标准输出只承载 JSON。

    def send(value):
        protocol.write(PREFIX + json.dumps(value, ensure_ascii=False) + "\n")
        protocol.flush()

    from voice_audio import SpeechOutput

    try:
        output = SpeechOutput(args)
        output.prepare()
        send({"ready": True})
        for line in sys.stdin:
            try:
                value = json.loads(line)
                send({"text": output.process(Path(value["path"]), False)})
            except Exception as exc:
                send({"error": str(exc)})
    except Exception as exc:
        send({"error": str(exc)})


if __name__ == "__main__":
    worker()
