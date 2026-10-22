"""常驻接收器基础设施：单实例互斥锁、滚动日志、状态文件和断线重连。

tools/background.py 发出独立诊断实例的启动/停止请求，本模块在接收器进程内部执行这些约定。
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import logging
import os
import sys
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from voice_discovery import read_json, write_json

DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "runtime"


def mutex_name(folder: Path) -> str:
    """同一状态目录共用一把锁；路径忽略大小写，避免 Windows 下重复启动。"""
    digest = hashlib.sha256(str(folder.resolve()).casefold().encode()).hexdigest()[:24]
    return "Local\\ibookKeyboardVoice_" + digest


def kernel():
    """声明 Win32 函数签名，防止 64 位系统上的 HANDLE 被截断。"""
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
    api.CreateMutexW.restype = w.HANDLE
    api.OpenMutexW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    api.OpenMutexW.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    return api


def is_running(folder: Path) -> bool:
    """尝试打开命名互斥锁，判断该目录对应的接收器是否还活着。"""
    api = kernel()
    handle = api.OpenMutexW(0x00100000, False, mutex_name(folder))
    if not handle:
        return False
    api.CloseHandle(handle)
    return True


class SingleInstance:
    """持有互斥锁直到进程结束；Windows 会在进程异常退出时自动释放句柄。"""

    def __init__(self, folder: Path):
        self.api = kernel()
        self.handle = self.api.CreateMutexW(None, False, mutex_name(folder))
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS：已有实例占用此锁。
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise RuntimeError("Background receiver is already running")

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class LogStream:
    """按行把 print/stdout/stderr 转给日志，供无控制台的 pythonw 使用。"""

    def __init__(self, logger, level):
        self.logger, self.level = logger, level
        self.pending = ""

    def write(self, text):
        self.pending += text
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            if line.strip():
                self.logger.log(self.level, line)
        return len(text)

    def flush(self):
        if self.pending.strip():
            self.logger.log(self.level, self.pending)
        self.pending = ""


def configure_logging(folder: Path):
    """日志单文件最多 1 MiB，保留 3 个备份，正常输出和错误分级记录。"""
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / "receiver.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("keyboard_voice")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    sys.stdout = LogStream(logger, logging.INFO)
    sys.stderr = LogStream(logger, logging.ERROR)


class Runtime:
    """持久化运行状态，并校验 stop.json 是否针对本次进程。

    token 每次启动都更新，防止历史 stop.json 误停刚启动的新实例。
    connected 表示蓝牙连接状态；status 进一步描述模型加载/录音/传输等阶段。
    """

    def __init__(self, folder: Path):
        self.folder = folder
        self.value = {
            "pid": os.getpid(),
            "token": uuid.uuid4().hex,
            "status": "starting",
            "connected": False,
            "address": None,
            "last_error": None,
        }
        self.update()

    def update(self, **fields):
        """合并变化并刷新心跳时间；外部通过 status.json 查看进度。"""
        self.value.update(fields)
        self.value["updated_at"] = time.time()
        write_json(self.folder / "status.json", self.value)

    def stop_requested(self):
        return read_json(self.folder / "stop.json").get("token") == self.value["token"]


async def run_daemon(args, output, receive):
    """持续接收并重连；每 0.5 秒检查停止请求，退出时取消接收会话。"""
    runtime = args.runtime

    async def connections():
        failures = 0
        while True:
            try:
                runtime.update(status="searching", connected=False)
                await receive(args, output)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                runtime.update(status="waiting", connected=False, last_error=str(exc))
                print(f"Connection unavailable: {exc}; retrying.", flush=True)
                # 失败后跳过旧地址缓存，重新查询已配对设备或扫描广播。
                args.ignore_cache = True
                # 线性增加等待时间，最多 30 秒，避免设备关机时频繁重试。
                await asyncio.sleep(min(args.retry_delay * failures, 30))

    task = asyncio.create_task(connections())
    try:
        while not task.done():
            if runtime.stop_requested():
                break
            runtime.update()
            await asyncio.sleep(0.5)
        if task.done():
            await task
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        runtime.update(status="stopped", connected=False)
