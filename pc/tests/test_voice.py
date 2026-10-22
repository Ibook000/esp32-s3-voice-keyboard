from __future__ import annotations

import argparse
import asyncio
import binascii
import contextlib
import io
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_audio as audio
import voice_protocol as p
import voice_receiver as receiver
import windows_input


def block(count=320, predictor=0, index=0, data=None):
    payload = struct.pack("<HhBB", count, predictor, index, 0)
    payload += bytes(count // 2) if data is None else data
    return payload + struct.pack("<H", binascii.crc_hqx(payload, 0xFFFF))


def status(state=p.State.SENDING, session=1, frames=2, error=0, flags=0):
    return p.STATUS.pack(
        1, 0x10, state, error, session, frames, 16000, 320, frames * 320, 10, flags
    )


def fragments(data, sequence=0, size=12, session=1):
    return [
        p.FRAGMENT.pack(1, 0x20, session, sequence, offset) + data[offset : offset + size]
        for offset in range(0, len(data), size)
    ]


class ProtocolTests(unittest.TestCase):
    def test_firmware_service_uuids_match_receiver(self):
        header = (Path(__file__).resolve().parents[2] / "include" / "voice_protocol.h").read_text(
            encoding="utf-8"
        )
        for name in ("SERVICE_UUID", "CONTROL_UUID", "AUDIO_UUID", "STATUS_UUID"):
            match = re.search(rf'{name}\[\]\s*=\s*"([^"]+)"', header)
            self.assertIsNotNone(match, name)
            self.assertEqual(match.group(1), getattr(p, name))

    def test_known_ima_vector_low_nibble_first(self):
        # At step index zero, codes 1,2,3,4 add 1,3,4,7 respectively.
        pcm = struct.unpack("<5h", p.decode_block(block(5, data=bytes([0x21, 0x43]))))
        self.assertEqual(pcm, (0, 1, 4, 8, 15))

    def test_silence(self):
        self.assertEqual(p.decode_block(block()), bytes(640))

    def test_bounds_crc_and_truncation(self):
        cases = [b"", block(0), block(321), block(index=89), block()[:-1]]
        corrupt = bytearray(block())
        corrupt[20] ^= 1
        cases.append(corrupt)
        for invalid in cases:
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                p.decode_block(invalid)

    def test_mtu23_fragmentation_and_lost_ack(self):
        clip = p.ClipAssembler(1)
        clip.update(p.DeviceStatus.parse(status(frames=1)))
        packets = fragments(block())
        for packet in packets[:-1]:
            self.assertIsNone(clip.feed(packet))
        ack = clip.feed(packets[-1])
        self.assertEqual(ack, struct.pack("<BHH", p.ACK, 1, 0))
        # Every fragment of a completed retransmission may be ACKed, but never appended.
        for packet in packets:
            self.assertEqual(clip.feed(packet), ack)
        clip.update(p.DeviceStatus.parse(status(p.State.COMPLETE, frames=1)))
        self.assertTrue(clip.complete)
        self.assertEqual(clip.pcm, bytes(640))

    def test_dropped_fragment_recovers_on_retry(self):
        clip = p.ClipAssembler(1)
        packets = fragments(block())
        for i, packet in enumerate(packets):
            if i != 3:
                self.assertIsNone(clip.feed(packet))
        self.assertEqual(clip.next_sequence, 0)
        for packet in packets:
            ack = clip.feed(packet)
        self.assertIsNotNone(ack)
        self.assertEqual(clip.next_sequence, 1)

    def test_corrupt_frame_is_not_acked(self):
        clip = p.ClipAssembler(1)
        bad = bytearray(block())
        bad[50] ^= 1
        with self.assertRaises(ValueError):
            for packet in fragments(bad):
                clip.feed(packet)
        self.assertEqual(clip.next_sequence, 0)
        for packet in fragments(block()):
            ack = clip.feed(packet)
        self.assertIsNotNone(ack)

    def test_old_session_future_frame_and_false_completion(self):
        clip = p.ClipAssembler(2)
        self.assertIsNone(clip.feed(fragments(block(), session=1)[0]))
        self.assertIsNone(clip.feed(fragments(block(), session=2, sequence=1)[0]))
        with self.assertRaises(ValueError):
            clip.update(p.DeviceStatus.parse(status(p.State.COMPLETE, session=2)))

    def test_status_rejects_invalid_metadata(self):
        data = bytearray(status())
        data[8:10] = struct.pack("<H", 48000)
        with self.assertRaises(ValueError):
            p.DeviceStatus.parse(data)
        with self.assertRaises(ValueError):
            p.DeviceStatus.parse(status()[:-1])

    def test_cli_rejects_unbounded_recordings_and_implicit_typing(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for args in (
                ["--duration", "60"],
                ["--type-text"],
                ["--duration", "nan"],
                ["--timeout", "nan"],
            ):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    receiver.parse_args(args)

    @unittest.skipUnless(sys.platform == "win32", "Windows Unicode input")
    def test_unicode_input_layout_without_injecting_real_keys(self):
        import ctypes

        send = mock.Mock(return_value=2)
        with mock.patch.object(ctypes, "WinDLL", return_value=SimpleNamespace(SendInput=send)):
            windows_input.type_text_windows("中😀")
        self.assertEqual(send.call_count, 3)  # BMP code unit + surrogate pair.
        codes = []
        for args, _ in send.call_args_list:
            count, events, size = args
            self.assertEqual(count, 2)
            self.assertEqual(size, 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
            self.assertEqual(events[0].value.keyboard.flags, 4)
            self.assertEqual(events[1].value.keyboard.flags, 6)
            codes.append(events[0].value.keyboard.scan)
        self.assertEqual(codes, [0x4E2D, 0xD83D, 0xDE00])

    def test_transcription_writes_text_but_does_not_type_by_default(self):
        args = argparse.Namespace(transcribe=True, model="fake", language="zh", type_text=False)
        output = receiver.SpeechOutput(args)
        output.model = mock.Mock()
        output.model.transcribe.return_value = (iter([SimpleNamespace(text="测试语音")]), None)
        array = mock.MagicMock()
        normalized = array.astype.return_value.__truediv__.return_value
        numpy = SimpleNamespace(frombuffer=mock.Mock(return_value=array), float32=object())
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(audio, "type_text_windows") as inject,
            mock.patch.dict(sys.modules, {"numpy": numpy}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            path = Path(folder) / "test.wav"
            receiver.save_wav(path, bytes(640))
            output.process(path, False)
            self.assertEqual(path.with_suffix(".txt").read_text(encoding="utf-8"), "测试语音\n")
            numpy.frombuffer.assert_called_once_with(bytes(640), dtype="<i2")
            self.assertIs(output.model.transcribe.call_args.args[0], normalized)
            inject.assert_not_called()


class RecordingCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_starts_then_stops_using_fresh_device_state(self):
        client = SimpleNamespace(
            write_gatt_char=mock.AsyncMock(),
            read_gatt_char=mock.AsyncMock(
                side_effect=[status(p.State.RECORDING, frames=0), status(p.State.SENDING)]
            ),
        )
        current = p.DeviceStatus.parse(status(p.State.IDLE, frames=0))
        packet = await receiver.handle_recording_command(client, "", current)
        current = p.DeviceStatus.parse(packet)
        self.assertEqual(current.state, p.State.RECORDING)
        packet = await receiver.handle_recording_command(client, "", current)
        self.assertEqual(p.DeviceStatus.parse(packet).state, p.State.SENDING)
        self.assertEqual(
            client.write_gatt_char.call_args_list,
            [
                mock.call(p.CONTROL_UUID, bytes([p.START]), response=True),
                mock.call(p.CONTROL_UUID, bytes([p.STOP]), response=True),
            ],
        )

    async def test_busy_or_unknown_commands_do_not_start_another_clip(self):
        client = SimpleNamespace(write_gatt_char=mock.AsyncMock(), read_gatt_char=mock.AsyncMock())
        with contextlib.redirect_stdout(io.StringIO()):
            for state, command in (
                (p.State.SENDING, ""),
                (p.State.RECORDING, "start"),
                (p.State.RECORDING, "n"),
                (p.State.IDLE, "unknown"),
            ):
                with self.subTest(state=state, command=command):
                    current = p.DeviceStatus.parse(status(state, frames=0))
                    self.assertIsNone(
                        await receiver.handle_recording_command(client, command, current)
                    )
        client.write_gatt_char.assert_not_called()
        client.read_gatt_char.assert_not_called()

    async def test_test_tone_and_cancellation_remain_available(self):
        client = SimpleNamespace(
            write_gatt_char=mock.AsyncMock(),
            read_gatt_char=mock.AsyncMock(return_value=status(p.State.IDLE, frames=0)),
        )
        await receiver.handle_recording_command(client, "n", None)
        sending = p.DeviceStatus.parse(status(p.State.SENDING))
        await receiver.handle_recording_command(client, "x", sending)
        self.assertEqual(
            client.write_gatt_char.call_args_list,
            [
                mock.call(p.CONTROL_UUID, bytes([p.TEST_TONE]), response=True),
                mock.call(p.CONTROL_UUID, bytes([p.CANCEL]), response=True),
            ],
        )


class CppCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("g++ unavailable for firmware codec interoperability test")
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.temp.name) / "encoder.exe"
        root = Path(__file__).resolve().parents[2]
        subprocess.run(
            [
                compiler,
                "-std=c++11",
                "-static",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(root / "include"),
                str(Path(__file__).with_name("encoder_fixture.cpp")),
                "-o",
                str(cls.exe),
            ],
            check=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def encode(self, samples):
        data = struct.pack(f"<H{len(samples)}h", len(samples), *samples)
        return subprocess.run([str(self.exe)], input=data, check=True, capture_output=True).stdout

    def test_firmware_encoder_to_python_decoder(self):
        vectors = [
            [0] * 320,
            [32767] * 320,
            [-32768] * 320,
            list(range(-16000, 16000, 100)),
            [int(5000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(320)],
        ]
        for samples in vectors:
            with self.subTest(first=samples[0]):
                encoded = self.encode(samples)
                self.assertEqual(len(encoded), p.BLOCK_BYTES)
                decoded = struct.unpack("<320h", p.decode_block(encoded))
                self.assertEqual(decoded[0], samples[0])
                error = sum((a - b) ** 2 for a, b in zip(samples, decoded)) / len(samples)
                self.assertLess(math.sqrt(error), 350)

    def test_single_odd_and_short_blocks(self):
        for samples in ([1234], [-120, 0], [100, 101, 104, 108, 115]):
            decoded = struct.unpack(f"<{len(samples)}h", p.decode_block(self.encode(samples)))
            self.assertEqual(decoded[0], samples[0])
            self.assertEqual(len(decoded), len(samples))


class FakeClient:
    def __init__(self, _, disconnected_callback=None, **kwargs):
        self.disconnected_callback = disconnected_callback
        self.is_connected = False
        self.services = SimpleNamespace(
            get_service=lambda uuid: object(), get_characteristic=lambda uuid: object()
        )
        self.mtu_size = 23
        self.callbacks = {}
        self.commands = []
        self.packet = status(p.State.IDLE, frames=0)
        self.ack = asyncio.Event()
        self.retries = 0
        self.dropped_ack = False
        self.task = None
        self.disconnect_during_transfer = False

    async def __aenter__(self):
        self.is_connected = True
        return self

    async def __aexit__(self, *args):
        self.is_connected = False
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def read_gatt_char(self, uuid):
        return self.packet

    def publish(self, state, frames=2):
        self.packet = status(state, frames=frames)
        self.callbacks[p.STATUS_UUID](None, self.packet)

    async def write_gatt_char(self, uuid, data, response):
        self.commands.append(bytes(data))
        if data[0] == p.START:
            self.publish(p.State.RECORDING, 0)
        elif data[0] == p.STOP:
            self.publish(p.State.SENDING)
            self.task = asyncio.create_task(self.transfer())
        elif data[0] == p.ACK:
            # Drop the first ACK to force replay of a fully decoded block.
            if not self.dropped_ack:
                self.dropped_ack = True
            else:
                self.ack.set()

    async def transfer(self):
        for sequence in range(2):
            for attempt in range(5):
                self.ack.clear()
                for i, packet in enumerate(fragments(block(), sequence=sequence)):
                    if self.disconnect_during_transfer and i == 3:
                        self.is_connected = False
                        self.disconnected_callback(self)
                        return
                    if sequence == 0 and attempt == 0 and i == 3:
                        continue  # Drop a fragment to force full-frame retransmission.
                    self.callbacks[p.AUDIO_UUID](None, packet)
                try:
                    await asyncio.wait_for(self.ack.wait(), 0.04)
                    break
                except asyncio.TimeoutError:
                    self.retries += 1
            else:
                raise RuntimeError("Fake peripheral did not receive ACK")
        # Deliberately lose the completion notification; desktop must poll it.
        self.packet = status(p.State.COMPLETE)


class EndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_daemon_receives_button_recording_without_pc_start_commands(self):
        # 模拟键盘实体按钮触发录音；PC 常驻模式只接收通知并发送音频 ACK。
        with tempfile.TemporaryDirectory() as folder:
            args = receiver.parse_args([])
            args.daemon = True
            args.output = Path(folder)
            fake = FakeClient(None)
            ready = asyncio.Event()
            processed = asyncio.Event()
            loop = asyncio.get_running_loop()
            output = mock.Mock()
            output.process.side_effect = lambda *_: loop.call_soon_threadsafe(processed.set)

            async def read_status(uuid):
                ready.set()
                return fake.packet

            fake.read_gatt_char = read_status

            def factory(*args, **kwargs):
                fake.disconnected_callback = kwargs["disconnected_callback"]
                return fake

            async def press_button_and_send():
                await ready.wait()
                fake.publish(p.State.RECORDING, frames=0)
                fake.publish(p.State.SENDING)
                await fake.transfer()
                await processed.wait()
                fake.is_connected = False
                fake.disconnected_callback(fake)

            with (
                mock.patch.object(receiver, "find_device", mock.AsyncMock(return_value="fake")),
                mock.patch.object(receiver, "console_reader") as console,
                mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=factory)}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                button_task = asyncio.create_task(press_button_and_send())
                try:
                    with self.assertRaisesRegex(RuntimeError, "disconnected"):
                        await asyncio.wait_for(receiver.receive(args, output), 5)
                    await button_task
                finally:
                    button_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await button_task

            console.assert_not_called()
            self.assertTrue(fake.commands)
            self.assertTrue(all(command[0] == p.ACK for command in fake.commands))
            files = list(Path(folder).glob("*.wav"))
            self.assertEqual(len(files), 1)
            output.process.assert_called_once_with(files[0], False)

    async def run_receiver(self, folder, disconnect=False):
        args = argparse.Namespace(
            address="AA:BB:CC:DD:EE:FF",
            name="ibook Keyboard",
            duration=0.02,
            test_tone=False,
            type_text=False,
            timeout=15,
            output=Path(folder),
        )
        fake = FakeClient(None)
        fake.disconnect_during_transfer = disconnect
        output = mock.Mock()
        with (
            mock.patch.object(receiver, "find_device", mock.AsyncMock(return_value="fake")),
            mock.patch.dict(
                sys.modules, {"bleak": SimpleNamespace(BleakClient=lambda *a, **kw: fake)}
            ),
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                # Factory replaces constructor, so wire the disconnect event here.
                def factory(*a, **kw):
                    self.assertEqual(kw["services"], [p.SERVICE_UUID])
                    fake.disconnected_callback = kw["disconnected_callback"]
                    return fake

                with mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakClient=factory)}):
                    await asyncio.wait_for(receiver.receive(args, output), 5)
        return fake, output

    async def test_receiver_retries_wav_and_lost_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            fake, output = await self.run_receiver(folder)
            files = list(Path(folder).glob("*.wav"))
            self.assertEqual(len(files), 1)
            with wave.open(str(files[0]), "rb") as wav:
                self.assertEqual(
                    (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()), (1, 16000, 2)
                )
                self.assertEqual(wav.getnframes(), 640)
                self.assertEqual(wav.readframes(640), bytes(1280))
            self.assertGreaterEqual(fake.retries, 2)
            self.assertNotIn(bytes([p.CANCEL]), fake.commands)
            output.process.assert_called_once_with(files[0], False)

    async def test_disconnect_does_not_save_partial_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await self.run_receiver(folder, disconnect=True)
            self.assertEqual(list(Path(folder).glob("*.wav")), [])


if __name__ == "__main__":
    unittest.main()
