from __future__ import annotations

import asyncio
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_voice import FakeClient

import keyboard_app as app
import keymap_protocol as km
import voice_protocol as vp


class SharedGatt(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.connects = 0
        self.disconnections = 0
        self.unsubscribed = []

    async def connect(self):
        self.connects += 1
        self.is_connected = True

    async def disconnect(self):
        self.disconnections += 1
        self.is_connected = False

    async def stop_notify(self, uuid):
        self.unsubscribed.append(uuid)
        self.callbacks.pop(uuid, None)

    async def read_gatt_char(self, uuid, **kwargs):
        if uuid == km.STATUS_UUID:
            data = bytearray([1, 0, 0, 0, 255, 0, 0, 0])
            for key in km.DEFAULT_KEYS:
                text = key["label"].encode()
                data.extend(
                    bytes([key["modifiers"], key["key"], len(text)]) + text + bytes(12 - len(text))
                )
            return bytes(data)
        return self.packet


class UnifiedTests(unittest.IsolatedAsyncioTestCase):
    async def wait_until(self, predicate):
        async with asyncio.timeout(4):
            while not predicate():
                await asyncio.sleep(0.01)

    async def test_same_connection_receives_button_audio_and_stops_only_voice(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = SharedGatt(None)
            engine = mock.Mock()
            engine.process.return_value = "测试文字"
            lease = mock.Mock()
            desk = app.KeyboardApp(
                Path(folder) / "runtime",
                client_factory=lambda *a, **k: peer,
                output_factory=lambda args: engine,
                lease_factory=lambda _: lease,
            )
            try:
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    mock.patch.object(app, "type_text_windows") as inject,
                ):
                    await desk.connect("44:1B:F6:FF:C9:1D")
                    await desk.configure_voice(
                        dict(app.DEFAULT_VOICE, enabled=True, type_text=False)
                    )
                    await self.wait_until(lambda: desk.voice.connected)
                    peer.publish(vp.State.RECORDING, frames=0)
                    peer.publish(vp.State.SENDING)
                    await peer.transfer()
                    await self.wait_until(lambda: bool(desk.voice.results))
                    self.assertEqual(desk.voice.results[0]["text"], "测试文字")
                    self.assertEqual(peer.connects, 1)
                    self.assertTrue(all(c[0] == vp.ACK for c in peer.commands))
                    self.assertEqual((await desk.read())["keys"], km.DEFAULT_KEYS)
                    await desk.configure_voice(dict(app.DEFAULT_VOICE, enabled=False))
                    self.assertTrue(peer.is_connected)
                    self.assertEqual(peer.disconnections, 0)
                    self.assertIn(vp.AUDIO_UUID, peer.unsubscribed)
                    inject.assert_not_called()
                    self.assertEqual(len(list((Path(folder) / "recordings").glob("*.wav"))), 1)
            finally:
                await desk.close()
            self.assertEqual(peer.disconnections, 1)
            lease.close.assert_called()

    async def test_late_recognition_never_types_after_disable(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = app.VoiceSession(folder)
            voice.settings = dict(app.DEFAULT_VOICE, enabled=True)
            voice.generation = 5
            with mock.patch.object(app, "type_text_windows") as inject:
                voice.completed(5, Path("clip.wav"), "你好")
                inject.assert_called_once_with("你好")
                await voice.pause()
                voice.settings["enabled"] = False
                voice.completed(5, Path("old.wav"), "旧任务")
                self.assertEqual(len(voice.results), 1)
                self.assertEqual(inject.call_count, 1)

    async def test_settings_persist_and_invalid_flags_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = app.VoiceSession(folder)
            options = dict(app.DEFAULT_VOICE, enabled=True, transcribe=False)
            await voice.configure(options, None, None)
            restored = app.VoiceSession(folder)
            self.assertTrue(restored.settings["enabled"])
            self.assertFalse(restored.settings["type_text"])
            self.assertEqual(restored.state, "waiting")
            with self.assertRaises(ValueError):
                app.validate_voice(dict(options, enabled="true"))

    async def test_legacy_receiver_prevents_duplicate_voice_subscription(self):
        with tempfile.TemporaryDirectory() as folder:
            voice = app.VoiceSession(
                folder, lease_factory=mock.Mock(side_effect=RuntimeError("already running"))
            )
            voice.settings = dict(app.DEFAULT_VOICE, enabled=True)
            with self.assertRaisesRegex(ValueError, "旧语音后台"):
                await voice.start(SimpleNamespace(is_connected=True), "44:1B:F6:FF:C9:1D")
            self.assertIsNone(voice.task)

    async def test_disconnect_reconnect_uses_the_same_selected_peer(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = SharedGatt(None)
            desk = app.KeyboardApp(Path(folder) / "runtime", client_factory=lambda *a, **k: peer)
            try:
                await desk.connect("44:1B:F6:FF:C9:1D")
                desk.reconnect = asyncio.create_task(desk.maintain())
                peer.is_connected = False
                await self.wait_until(lambda: peer.connects == 2)
                self.assertEqual(desk.address, "44:1B:F6:FF:C9:1D")
                await desk.disconnect()
                self.assertIsNone(desk.target)
                self.assertFalse(desk.connected)
            finally:
                await desk.close()

    async def test_failed_model_does_not_disconnect_keymap_and_can_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            peer = SharedGatt(None)
            engine = mock.Mock()
            engine.prepare.side_effect = RuntimeError("model unavailable")
            desk = app.KeyboardApp(
                Path(folder) / "runtime",
                client_factory=lambda *a, **k: peer,
                output_factory=lambda args: engine,
                lease_factory=lambda _: mock.Mock(),
            )
            try:
                await desk.connect("44:1B:F6:FF:C9:1D")
                with self.assertLogs(level="ERROR"):
                    await desk.configure_voice(dict(app.DEFAULT_VOICE, enabled=True))
                    await self.wait_until(lambda: desk.voice.state == "error")
                self.assertTrue(desk.connected)
                self.assertEqual(desk.voice.error, "model unavailable")
                engine.prepare.side_effect = None
                with contextlib.redirect_stdout(io.StringIO()):
                    await desk.configure_voice(dict(app.DEFAULT_VOICE, enabled=True))
                    await self.wait_until(lambda: desk.voice.connected)
                self.assertEqual(peer.connects, 1)
            finally:
                await desk.close()
