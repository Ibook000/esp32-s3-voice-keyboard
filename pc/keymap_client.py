"""异步 BLE 配置客户端；写入分片均不超过 MTU23 的 20 字节上限。"""

from __future__ import annotations

import asyncio
import re
import secrets
import time

from keymap_protocol import (
    ABORT,
    BEGIN,
    COMMIT,
    CONFIG_UUID,
    ERRORS,
    STAGE_ROTARY,
    STAGE_SLEEP,
    STATUS_UUID,
    VERSION,
    decode_response,
    stage_packet,
    validate_keys,
)
from voice_discovery import known_device, paired_windows_devices
from voice_protocol import SERVICE_UUID


class KeymapClient:
    def __init__(self, client_factory=None):
        self.factory = client_factory
        self.client = None
        self.address = None
        self.config = None
        self.lock = asyncio.Lock()

    @property
    def connected(self):
        return bool(self.client and self.client.is_connected)

    async def devices(self):
        async with self.lock:
            devices = []
            try:
                devices = await paired_windows_devices("ibook Keyboard")
            except Exception:
                pass
            if self.connected:
                devices.append(known_device(self.address, "ibook Keyboard"))
            if not devices:
                from bleak import BleakScanner

                discovered = await BleakScanner.discover(timeout=5, return_adv=True)
                devices = [
                    device
                    for device, adv in discovered.values()
                    if (adv.local_name or device.name or "").casefold() == "ibook keyboard"
                ]
            unique = {
                d.address.upper(): {
                    "address": d.address.upper(),
                    "name": d.name or "ibook Keyboard",
                }
                for d in devices
            }
            return list(unique.values())

    async def disconnect(self):
        async with self.lock:
            await self._disconnect()

    async def _disconnect(self):
        previous, self.client = self.client, None
        self.config = None
        if previous:
            await asyncio.wait_for(previous.disconnect(), 8)

    async def connect(self, address):
        if not isinstance(address, str) or not re.fullmatch(
            r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", address
        ):
            raise ValueError("请选择要连接的键盘。")
        async with self.lock:
            if self.connected and address.upper() == self.address:
                return await self._read()
            await self._disconnect()
            if self.factory is None:
                from bleak import BleakClient

                factory = BleakClient
            else:
                factory = self.factory
            client = factory(
                known_device(address, "ibook Keyboard"),
                services=[SERVICE_UUID],
                timeout=25,
                winrt={"use_cached_services": False},
            )
            self.client, self.address = client, address.upper()
            try:
                await asyncio.wait_for(client.connect(), 28)
                if any(
                    client.services.get_characteristic(uuid) is None
                    for uuid in (CONFIG_UUID, STATUS_UUID)
                ):
                    raise ValueError(
                        "键盘还没有配置接口。请先烧录新版固件，删除 Windows 旧配对并重新添加。"
                    )
                return await self._read()
            except BaseException:
                try:
                    await self._disconnect()
                except Exception:
                    pass
                raise

    async def _read(self):
        if not self.connected:
            raise ConnectionError("键盘尚未连接，请先连接键盘。")
        data = await asyncio.wait_for(self.client.read_gatt_char(STATUS_UUID, use_cached=False), 5)
        self.config = decode_response(bytes(data))
        return self.config

    async def read(self):
        async with self.lock:
            return await self._read()

    async def status(self):
        return {
            "connected": self.connected,
            "address": self.address,
            "config": self.config if self.connected else None,
        }

    async def _command(self, packet):
        await asyncio.wait_for(self.client.write_gatt_char(CONFIG_UUID, packet, response=True), 5)
        expected = (packet[2], packet[1], packet[3] if len(packet) > 3 else 255)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = await self._read()
            if (state["transaction"], state["operation"], state["index"]) == expected:
                if state["result"]:
                    raise ValueError(ERRORS.get(state["result"], "键盘拒绝了配置，请重新连接。"))
                return state
            await asyncio.sleep(0.04)
        raise TimeoutError("键盘没有确认配置，请重新连接后再试。")

    async def save_rotary(self, mode):
        if mode not in ("scroll", "volume"):
            raise ValueError("请选择滚动或音量模式。")
        async with self.lock:
            current = await self._read()
            if current.get("rotary_mode") is None:
                raise ValueError("请先烧录支持旋钮模式的新版固件，并删除旧蓝牙配对后重新添加。")
            return await self._save(current["keys"], mode)

    async def save_sleep(self, minutes):
        if type(minutes) is not int or not 0 <= minutes <= 120:
            raise ValueError("深睡时间为 0（关闭）或 1～120 分钟。")
        async with self.lock:
            current = await self._read()
            if current.get("sleep_minutes") is None:
                raise ValueError("请先烧录支持自动深睡的新版固件。")
            return await self._save(current["keys"], sleep_minutes=minutes)

    async def save(self, keys):
        keys = validate_keys(keys)
        async with self.lock:
            return await self._save(keys)

    async def _save(self, keys, rotary_mode=None, sleep_minutes=None):
        if not self.connected:
            raise ConnectionError("键盘已断开，草稿仍保留，请重新连接后保存。")
        tx = secrets.randbelow(255) + 1
        try:
            await self._command(bytes([VERSION, BEGIN, tx]))
            for index, binding in enumerate(keys):
                await self._command(stage_packet(tx, index, binding))
            if rotary_mode is not None:
                await self._command(
                    bytes([VERSION, STAGE_ROTARY, tx, int(rotary_mode == "volume")])
                )
            if sleep_minutes is not None:
                await self._command(
                    bytes([VERSION, STAGE_SLEEP, tx, sleep_minutes & 255, sleep_minutes >> 8])
                )
            state = await self._command(bytes([VERSION, COMMIT, tx]))
            if (
                state["keys"] != keys
                or (rotary_mode is not None and state.get("rotary_mode") != rotary_mode)
                or (sleep_minutes is not None and state.get("sleep_minutes") != sleep_minutes)
            ):
                raise ValueError("保存后读取校验不一致，请重新读取键盘配置。")
            return state
        except BaseException:
            if self.connected:
                try:
                    await asyncio.wait_for(
                        self.client.write_gatt_char(
                            CONFIG_UUID, bytes([VERSION, ABORT, tx]), response=True
                        ),
                        2,
                    )
                except Exception:
                    pass
            raise
