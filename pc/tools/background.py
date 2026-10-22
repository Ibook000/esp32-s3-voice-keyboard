"""Windows 后台管理入口：启动、停止、查看语音接收器状态。

日常使用 pc/start.cmd；本工具只供旧版独立语音接收器的诊断。
本文件只管理进程；实际蓝牙接收入口仍为 voice_receiver.py。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

PC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PC_ROOT))

from voice_background import DEFAULT_STATE_DIR, is_running  # noqa: E402
from voice_discovery import read_json, write_json  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    """后台启动默认使用 tiny 模型；前台接收器默认使用 small。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop", "status"), help="启动 / 停止 / 状态")
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR, help="状态和日志目录，默认 pc/runtime"
    )
    parser.add_argument("--model", default="tiny", help="启动时使用的模型名称或本地目录，默认 tiny")
    parser.add_argument(
        "--no-type-text", action="store_true", help="启动时只保存识别文字，不输入窗口"
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        parser.error("The hidden background launcher currently supports Windows")
    return args


def receiver_status(folder: Path) -> dict:
    """磁盘状态可能是上次遗留的；互斥锁才是进程是否仍在运行的依据。"""
    state = read_json(folder / "status.json")
    state["running"] = is_running(folder)
    if not state["running"]:
        state.update(status="stopped", connected=False)
    return state


def stop_receiver(folder: Path) -> None:
    """用当前实例 token 请求正常退出，给已保存录音的识别任务留出收尾时间。"""
    if not is_running(folder):
        print("Background receiver is stopped.")
        return
    state = read_json(folder / "status.json")
    write_json(folder / "stop.json", {"token": state.get("token")})
    for _ in range(50):
        if not is_running(folder):
            print("Background receiver stopped.")
            return
        time.sleep(0.1)
    print("Stop requested; current transcription may still be finishing.")


def start_receiver(folder: Path, model: str, type_text: bool) -> None:
    """通过同一 Python 环境的 pythonw.exe 启动隐藏进程，避免常驻控制台。"""
    if is_running(folder):
        print("Background receiver is already running.")
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise RuntimeError("Use the project's .venv/Scripts/python.exe to launch")
    command = [
        str(pythonw),
        str(PC_ROOT / "voice_receiver.py"),
        "--daemon",
        "--transcribe",
        "--model",
        model,
        "--state-dir",
        str(folder),
    ]
    if type_text:
        command.append("--type-text")
    process = subprocess.Popen(
        command,
        cwd=PC_ROOT.parent,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    for _ in range(50):
        if is_running(folder):
            print(f"Background receiver started. Logs: {folder / 'receiver.log'}")
            # 取得互斥锁表示进程已启动；模型加载和蓝牙连接可能还在进行中。
            return
        if process.poll() is not None:
            raise RuntimeError(
                f"Receiver exited ({process.returncode}); check {folder / 'receiver.log'}"
            )
        time.sleep(0.1)
    raise RuntimeError("Receiver did not initialize; check background status/logs")


def main(argv=None) -> int:
    """只做命令分发；三个操作可分别阅读和维护。"""
    args = parse_args(argv)
    folder = args.state_dir.resolve()
    if args.action == "status":
        print(json.dumps(receiver_status(folder), ensure_ascii=True, indent=2))
    elif args.action == "stop":
        stop_receiver(folder)
    else:
        start_receiver(folder, args.model, not args.no_type_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
