"""统一工作台：快捷键与语音复用同一 BLE 连接，网页只负责操作与展示。

VoiceSession 借用 KeymapClient 的连接；停用语音仅退订音频，不断开快捷键配置。
模型加载与识别放在线程中，语音协议、重传和 WAV 保存沿用 voice_receiver。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import logging
import time
from pathlib import Path

from keymap_client import KeymapClient
from speech_process import ProcessSpeechOutput
from voice_background import SingleInstance
from voice_discovery import read_json, write_json
from voice_receiver import receive
from windows_input import type_text_windows

DEFAULT_VOICE = {"enabled": False, "transcribe": True, "type_text": True, "model": "tiny"}


def validate_voice(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_VOICE):
        raise ValueError("请提供完整的语音设置。")
    if any(type(value[key]) is not bool for key in ("enabled", "transcribe", "type_text")):
        raise ValueError("语音开关格式无效。")
    if value["model"] not in ("tiny", "base", "small"):
        raise ValueError("请选择 tiny、base 或 small 模型。")
    result = dict(value)
    if not result["transcribe"]:
        result["type_text"] = False
    return result


class SessionOutput:
    """识别线程只发回结果；主循环核对启用状态后才允许自动输入。"""

    def __init__(self, owner, args, generation):
        self.owner, self.generation = owner, generation
        self.engine = owner.output_factory(args)
        self.loop = asyncio.get_running_loop()

    def prepare(self):
        self.engine.prepare()

    def post(self, callback, *args):
        if not self.loop.is_closed():
            with contextlib.suppress(RuntimeError):
                self.loop.call_soon_threadsafe(callback, self.generation, *args)

    def process(self, path, test_tone):
        self.post(self.owner.processing, 1)
        try:
            text = self.engine.process(path, test_tone)
            self.post(self.owner.completed, path, text)
        except Exception as exc:
            self.post(self.owner.failed, str(exc))
        finally:
            self.post(self.owner.processing, -1)


class VoiceSession:
    def __init__(self, folder, *, output_factory=ProcessSpeechOutput, lease_factory=SingleInstance):
        self.folder = Path(folder)
        self.output_factory, self.lease_factory = output_factory, lease_factory
        try:
            self.settings = validate_voice(read_json(self.folder / "voice-settings.json"))
        except ValueError:
            self.settings = dict(DEFAULT_VOICE)
        self.state = "waiting" if self.settings["enabled"] else "disabled"
        self.error = None
        self.connected = False
        self.results = []
        self.pending = 0
        self.generation = 0
        self.task = None
        self.lease = None
        self.output = None

    def snapshot(self):
        return {
            "settings": dict(self.settings),
            "status": self.state,
            "connected": self.connected,
            "processing": self.pending > 0,
            "last_error": self.error,
            "results": copy.deepcopy(self.results),
        }

    def update(self, **fields):
        """接收器原有 Runtime 接口：把录音/发送状态映射到网页内存状态。"""
        if "status" in fields:
            self.state = fields["status"]
        if "connected" in fields:
            self.connected = fields["connected"]
        if "last_error" in fields:
            self.error = fields["last_error"]

    def processing(self, generation, delta):
        if generation == self.generation:
            self.pending = max(0, self.pending + delta)

    def failed(self, generation, error):
        if generation == self.generation:
            self.error = error

    def completed(self, generation, path, text):
        # 禁用、换设备、关闭服务后，旧线程完成的识别不得再注入文字。
        if generation != self.generation or not self.settings["enabled"]:
            return
        result = {
            "time": time.time(),
            "file": path.name,
            "text": text,
            "typed": False,
            "error": None,
        }
        if text and self.settings["type_text"]:
            try:
                type_text_windows(text)
                result["typed"] = True
            except Exception as exc:
                result["error"] = self.error = str(exc)
        self.results.insert(0, result)
        del self.results[8:]

    async def pause(self):
        self.generation += 1
        self.connected = False
        self.pending = 0
        output, self.output = self.output, None
        if output:
            close = getattr(output.engine, "close", None)
            if close:
                close()
        task, self.task = self.task, None
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.lease:
            self.lease.close()
            self.lease = None
        self.state = "waiting" if self.settings["enabled"] else "disabled"

    async def start(self, client, address):
        if not self.settings["enabled"] or not client or not client.is_connected:
            self.state = "waiting" if self.settings["enabled"] else "disabled"
            return
        if self.task and not self.task.done():
            return
        # 与旧版语音后台共用互斥锁，禁止两个接收器订阅同一条音频流。
        try:
            self.lease = self.lease_factory(self.folder)
        except RuntimeError as exc:
            self.state = "error"
            self.error = "旧语音后台仍在运行，请先执行 tools/background.py stop。"
            raise ValueError("旧语音后台仍在运行，请先停止，再在工作台启用语音。") from exc
        self.error = None
        self.generation += 1
        generation = self.generation
        options = self.settings.copy()
        args = argparse.Namespace(
            name="ibook Keyboard",
            address=address,
            state_dir=self.folder,
            output=self.folder.parent / "recordings",
            daemon=True,
            duration=None,
            test_tone=False,
            timeout=120,
            transcribe=options["transcribe"],
            type_text=False,
            model=options["model"],
            language="zh",
            runtime=self,
        )

        async def run():
            try:
                self.state = "loading_model" if options["transcribe"] else "subscribing"
                output = SessionOutput(self, args, generation)
                self.output = output
                await asyncio.to_thread(output.prepare)
                if generation != self.generation:
                    return
                self.state = "subscribing"
                await receive(args, output, client=client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state, self.error, self.connected = "error", str(exc), False
                logging.exception("Voice session failed")
            finally:
                if self.lease:
                    self.lease.close()
                    self.lease = None

        self.task = asyncio.create_task(run())

    async def configure(self, value, client, address):
        options = validate_voice(value)
        # 设置写入失败不停止当前会话；没有变化时可用于重试失败的订阅/模型加载。
        restart = options != self.settings or self.error is not None
        if options != self.settings:
            write_json(self.folder / "voice-settings.json", options)
        if restart:
            await self.pause()
            self.settings = options
            self.error = None
        await self.start(client, address)
        return self.snapshot()


class KeyboardApp(KeymapClient):
    """Web 唯一客户端，连接、断开、重连时一起管理语音订阅。"""

    def __init__(self, folder, client_factory=None, **voice_options):
        super().__init__(client_factory)
        self.voice = VoiceSession(folder, **voice_options)
        self.lifecycle = asyncio.Lock()
        self.reconnect = None
        self.target = read_json(Path(folder) / "workbench.json").get("address")
        self.closing = False

    async def connect(self, address):
        async with self.lifecycle:
            return await self._connect(address)

    async def _connect(self, address):
        result = await super().connect(address)
        self.target = self.address
        try:
            await self.voice.start(self.client, self.address)
        except ValueError as exc:
            self.voice.state, self.voice.error = "error", str(exc)
        write_json(self.voice.folder / "workbench.json", {"address": self.address})
        return result

    async def _disconnect(self):
        await self.voice.pause()
        await super()._disconnect()

    async def disconnect(self):
        self.target = None
        async with self.lifecycle:
            await super().disconnect()

    async def configure_voice(self, options):
        async with self.lifecycle:
            return await self.voice.configure(options, self.client, self.address)

    async def status(self):
        result = await super().status()
        result["voice"] = self.voice.snapshot()
        return result

    async def maintain(self):
        """使用用户选定的设备地址重连；不切换到另一台同名键盘。"""
        while not self.closing:
            await asyncio.sleep(3)
            if self.target and not self.connected:
                address = self.target
                try:
                    async with self.lifecycle:
                        if self.target == address and not self.connected:
                            await self._connect(address)
                except Exception:
                    self.voice.connected = False
                    self.voice.state = "waiting" if self.voice.settings["enabled"] else "disabled"
                    logging.info("Keyboard reconnect pending", exc_info=True)

    async def close(self):
        self.closing = True
        if self.reconnect:
            self.reconnect.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reconnect
        await self.disconnect()
