"""本地语音引擎的受管理子进程，原生扩展崩溃不会带走 BLE/Web 主进程。

两种工作方式：
- ``ProcessSpeechOutput``：整段 WAV 进、整段文字出，命令行与旧引擎使用。
- ``StreamSpeechOutput``：流式 IPC，20 ms 一帧 PCM 进，句子定稿就回一段文字。
仅传递音频字节和识别文字，不传送到外部服务；禁用语音时主动结束子进程。
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
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


class StreamSpeechOutput:
    """流式引擎：边收 PCM 边识别，句子定稿立刻回一段文字。

    读线程常驻消费子进程 stdout，识别结果通过 attach() 注册的回调送出去，
    ``end`` 只等待最终响应，不会因为中间结果堵住管道。
    """

    streaming = True

    def __init__(self, args, threads: int = 2):
        self.args = args
        self.threads = threads
        self.streaming = bool(getattr(args, "transcribe", True))
        self.process_handle = None
        self.closed = False
        self.write_lock = threading.Lock()
        self.responses: queue.Queue = queue.Queue()
        self.reader = None
        self.on_commit = None
        self.on_partial = None
        self.last_partial = ""
        self.parts: list[str] = []

    def attach(self, on_commit, on_partial) -> None:
        self.on_commit, self.on_partial = on_commit, on_partial

    def prepare(self) -> None:
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
            "--stream-worker",
            "--model",
            self.args.model,
            "--threads",
            str(self.threads),
        ]
        self.spawn(command, folder / "asr-worker.log")

    def spawn(self, command, log_path: Path) -> None:
        """启动子进程并等它 ready；测试可传入替代命令验证 IPC。"""
        environment = os.environ.copy()
        environment["HF_HUB_DISABLE_XET"] = "1"
        with Path(log_path).open("ab") as log:
            self.process_handle = subprocess.Popen(
                [str(item) for item in command],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                encoding="utf-8",
                env=environment,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        self.reader = threading.Thread(
            target=self._read_loop, name="asr-reader", daemon=True
        )
        self.reader.start()
        if self.closed:
            self.close()
            raise RuntimeError("语音已停止。")
        try:
            if not self._response(60, "语音引擎未能完成初始化。").get("ready"):
                raise RuntimeError("语音引擎未能完成初始化。")
        except BaseException:
            self.close()
            raise

    def _read_loop(self) -> None:
        process = self.process_handle
        for line in process.stdout:
            if not line.startswith(PREFIX):
                continue
            try:
                message = json.loads(line[len(PREFIX) :])
            except json.JSONDecodeError:
                continue
            if "commit" in message:
                self.parts.append(message["commit"])
                if self.on_commit:
                    self.on_commit(message["commit"])
            elif "partial" in message:
                self.last_partial = message["partial"]
                if self.on_partial:
                    self.on_partial(message["partial"])
            else:
                self.responses.put(message)
        code = process.wait()
        self.responses.put(
            {"error": f"语音引擎已退出（0x{code & 0xFFFFFFFF:08X}），请重新应用语音设置。"}
        )

    def _response(self, timeout: float, fallback: str):
        try:
            message = self.responses.get(timeout=timeout)
        except queue.Empty as exc:
            raise RuntimeError(fallback) from exc
        if "error" in message:
            raise RuntimeError(message["error"])
        return message

    def _send(self, payload) -> None:
        with self.write_lock:
            if self.closed or self.process_handle is None:
                raise RuntimeError("语音已停止。")
            try:
                self.process_handle.stdin.write(
                    json.dumps(payload, ensure_ascii=False) + "\n"
                )
                self.process_handle.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RuntimeError("语音引擎连接已关闭，请重新应用语音设置。") from exc

    def begin(self) -> None:
        self.parts.clear()
        self.last_partial = ""
        self._send({"cmd": "begin"})

    def push(self, pcm: bytes) -> None:
        if not pcm:
            return
        self._send({"cmd": "pcm", "b64": base64.b64encode(pcm).decode("ascii")})

    def process(self, path=None, test_tone: bool = False):
        """未启用识别时只保存录音，不需要模型参与。"""
        return None

    def end(self, path=None, test_tone: bool = False):
        if test_tone:
            return None
        self._send({"cmd": "end"})
        done = self._response(30, "语音引擎没有返回识别结果。")["done"]
        if self.on_partial:
            self.on_partial("")
        return done.get("text")

    def cancel(self) -> None:
        if self.closed:
            return
        with contextlib.suppress(RuntimeError):
            self._send({"cmd": "cancel"})

    def close(self) -> None:
        process = self.process_handle
        if process and not self.closed:
            with contextlib.suppress(RuntimeError):
                self._send({"cmd": "quit"})
        self.closed = True
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


def stream_worker():
    """流式子进程：读 JSON 命令，喂 PCM，句子定稿就回一段文字。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetErrorMode(0x8003)
    parser = argparse.ArgumentParser()
    parser.add_argument("--stream-worker", action="store_true")
    parser.add_argument("--model", default="zipformer-zh")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    protocol = sys.stdout
    sys.stdout = sys.stderr  # ONNX 运行时的输出进日志，stdout 只承载 JSON。

    def send(value):
        protocol.write(PREFIX + json.dumps(value, ensure_ascii=False) + "\n")
        protocol.flush()

    from stream_asr import StreamRecognizer

    try:
        recognizer = StreamRecognizer(args.model, args.threads)
        send({"ready": True})
    except Exception as exc:
        send({"error": str(exc)})
        return

    parts: list[str] = []
    text = ""
    last_partial = ""
    partial_at = 0.0
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            send({"error": "无法解析的引擎命令。"})
            continue
        command = message.get("cmd")
        try:
            if command == "begin":
                recognizer.begin()
                parts, text, last_partial, partial_at = [], "", "", 0.0
            elif command == "pcm":
                piece, done = recognizer.push(base64.b64decode(message["b64"]))
                if done:
                    if piece:
                        parts.append(piece)
                        text += piece
                        send({"commit": piece})
                    last_partial, partial_at = "", 0.0
                    send({"partial": ""})
                elif piece != last_partial:
                    now = time.monotonic()
                    if now - partial_at >= 0.15:
                        last_partial, partial_at = piece, now
                        send({"partial": piece})
            elif command == "end":
                tail = recognizer.end()
                if tail:
                    parts.append(tail)
                    text += tail
                    send({"commit": tail})
                send({"done": {"text": text, "tail": tail}})
                recognizer.begin()
                parts, text, last_partial, partial_at = [], "", "", 0.0
            elif command == "cancel":
                recognizer.begin()
                parts, text, last_partial, partial_at = [], "", "", 0.0
            elif command == "quit":
                break
            else:
                send({"error": "未知的引擎命令。"})
        except Exception as exc:
            send({"error": str(exc)})


if __name__ == "__main__":
    if "--stream-worker" in sys.argv:
        stream_worker()
    else:
        worker()
