from __future__ import annotations

import copy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keymap_client import KeymapClient
from keymap_protocol import (
    ABORT,
    BEGIN,
    COMMIT,
    CONFIG_UUID,
    DEFAULT_KEYS,
    STAGE_ROTARY,
    STAGE_SLEEP,
    STATUS_UUID,
    VERSION,
    decode_response,
    stage_packet,
    validate_keys,
)


class ProtocolTests(unittest.TestCase):
    def test_validation_rejects_oversize_control_and_unsupported_labels(self):
        self.assertEqual(validate_keys(copy.deepcopy(DEFAULT_KEYS)), DEFAULT_KEYS)
        for field, value in [
            ("label", "超长名称"),
            ("label", ""),
            ("label", "A\x00"),
            ("label", "😀"),
            ("modifiers", True),
            ("key", 0),
        ]:
            keys = copy.deepcopy(DEFAULT_KEYS)
            keys[0][field] = value
            with self.assertRaises(ValueError):
                validate_keys(keys)
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[0]["id"] = 2
        with self.assertRaises(ValueError):
            validate_keys(keys)
        for size in [0, 3, 52, 55]:
            with self.assertRaises(ValueError):
                decode_response(bytes(size))


class FirmwareTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("g++")
        if not compiler:
            raise unittest.SkipTest("g++ unavailable")
        root = Path(__file__).resolve().parents[2]
        cls.temp = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.temp.name) / "keymap.exe"
        result = subprocess.run(
            [
                compiler,
                "-std=c++11",
                "-static",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(root / "include"),
                str(root / "src/keymap.cpp"),
                str(root / "pc/tests/keymap_transaction_fixture.cpp"),
                "-o",
                str(cls.exe),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.process = subprocess.Popen(
            [str(self.exe)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.last = self.exchange("reboot")

    def tearDown(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()

    def exchange(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        response = bytes.fromhex(self.process.stdout.readline().strip())
        self.last = response
        return response

    def command(self, packet, can=True, store=True):
        return decode_response(self.exchange(f"{packet.hex()} {int(can)} {int(store)}"))

    def prepare(self, keys, tx=7):
        self.command(bytes([VERSION, BEGIN, tx]))
        for index, binding in enumerate(keys):
            packet = stage_packet(tx, index, binding)
            self.assertLessEqual(len(packet), 20)
            self.assertEqual(self.command(packet)["result"], 0)

    def test_sleep_config_rejects_invalid_values_and_busy_commit(self):
        self.prepare(DEFAULT_KEYS)
        self.assertEqual(self.command(bytes([VERSION, STAGE_SLEEP, 7, 121, 0]))["result"], 1)
        self.assertEqual(self.command(bytes([VERSION, STAGE_SLEEP, 7, 1, 1]))["result"], 1)
        self.command(bytes([VERSION, STAGE_SLEEP, 7, 0, 0]))
        self.assertEqual(self.command(bytes([VERSION, COMMIT, 7]), can=False)["result"], 4)
        self.assertEqual(decode_response(self.exchange("reboot"))["sleep_minutes"], 10)
        for size in (53, 54):
            state = decode_response(self.last[:size])
            self.assertIsNone(state["sleep_minutes"])

    def test_rotary_mode_commits_reboots_and_key_only_save_preserves_mode(self):
        self.prepare(DEFAULT_KEYS)
        self.assertEqual(self.command(bytes([VERSION, STAGE_ROTARY, 7, 1]))["result"], 0)
        self.assertEqual(self.command(bytes([VERSION, COMMIT, 7]))["rotary_mode"], "volume")
        self.assertEqual(decode_response(self.exchange("reboot"))["rotary_mode"], "volume")
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[0].update(label="保存", key=22)
        self.prepare(keys)
        state = self.command(bytes([VERSION, COMMIT, 7]))
        self.assertEqual(state["rotary_mode"], "volume")
        self.assertEqual(state["keys"], keys)

    def test_rotary_mode_busy_failed_store_and_invalid_stage_keep_current(self):
        for can, store in [(False, True), (True, False)]:
            self.prepare(DEFAULT_KEYS)
            self.command(bytes([VERSION, STAGE_ROTARY, 7, 1]))
            self.assertNotEqual(
                self.command(bytes([VERSION, COMMIT, 7]), can=can, store=store)["result"], 0
            )
            self.assertEqual(decode_response(self.exchange("reboot"))["rotary_mode"], "scroll")
        self.prepare(DEFAULT_KEYS)
        self.assertEqual(self.command(bytes([VERSION, STAGE_ROTARY, 7, 2]))["result"], 1)

    async def test_python_client_saves_against_real_cpp_transaction_and_reboots(self):
        owner = self

        class FakeGatt:
            is_connected = True
            services = SimpleNamespace(get_characteristic=lambda _: object())

            async def read_gatt_char(self, uuid, **__):
                owner.assertEqual(uuid, STATUS_UUID)
                return owner.last

            async def write_gatt_char(self, uuid, data, **__):
                owner.assertEqual(uuid, CONFIG_UUID)
                owner.command(data)

            async def connect(self):
                pass

            async def disconnect(self):
                self.is_connected = False

        backend = KeymapClient(lambda *_, **__: FakeGatt())
        with patch("keymap_client.known_device", return_value="test-device"):
            await backend.connect("44:1B:F6:FF:C9:1D")
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[0].update(label="保存", key=22)
        result = await backend.save(keys)
        self.assertEqual(result["keys"], keys)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(decode_response(self.exchange("reboot"))["keys"], keys)
        result = await backend.save_rotary("volume")
        self.assertEqual(result["rotary_mode"], "volume")
        self.assertEqual(result["keys"], keys)
        result = await backend.save(keys)
        self.assertEqual(result["rotary_mode"], "volume")
        self.assertEqual(decode_response(self.exchange("reboot"))["rotary_mode"], "volume")
        result = await backend.save_sleep(5)
        self.assertEqual(result["sleep_minutes"], 5)
        self.assertEqual(result["rotary_mode"], "volume")
        result = await backend.save(keys)
        self.assertEqual(result["sleep_minutes"], 5)
        self.assertEqual(decode_response(self.exchange("reboot"))["sleep_minutes"], 5)
        result = await backend.save_sleep(0)
        self.assertEqual(result["sleep_minutes"], 0)
        self.assertEqual(result["keys"], keys)
        await backend.disconnect()

    async def test_incomplete_busy_failed_storage_and_foreign_transactions_leave_active_unchanged(
        self,
    ):
        self.command(bytes([VERSION, BEGIN, 4]))
        self.assertEqual(self.command(bytes([VERSION, COMMIT, 4]))["result"], 3)
        self.assertEqual(self.command(bytes([VERSION, BEGIN, 5]))["result"], 2)
        self.command(bytes([VERSION, ABORT, 4]))
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[1].update(label="全选", key=4)
        self.prepare(keys)
        busy = self.command(bytes([VERSION, COMMIT, 7]), can=False)
        self.assertEqual(busy["result"], 4)
        self.assertEqual(busy["keys"], DEFAULT_KEYS)
        failed = self.command(bytes([VERSION, COMMIT, 7]), store=False)
        self.assertEqual(failed["result"], 5)
        self.assertEqual(failed["keys"], DEFAULT_KEYS)
        self.assertEqual(decode_response(self.exchange("reboot"))["keys"], DEFAULT_KEYS)

    async def test_invalid_utf8_embedded_nul_and_keys_rejected_without_overwriting(self):
        self.command(bytes([VERSION, BEGIN, 7]))
        packets = [
            bytes([1, 2, 7, 0, 1, 6, 2, 0xC0, 0x80]),
            bytes([1, 2, 7, 0, 1, 6, 2, 65, 0]),
            bytes([1, 2, 7, 0, 1, 0, 1, 65]),
        ]
        for packet in packets:
            state = self.command(packet)
            self.assertEqual(state["result"], 1)
            self.assertEqual(state["keys"], DEFAULT_KEYS)

    async def test_cancelled_or_repeated_unchanged_save_does_not_increment_revision(self):
        self.prepare(DEFAULT_KEYS)
        state = self.command(bytes([VERSION, COMMIT, 7]))
        self.assertEqual(state["revision"], 0)
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[0]["key"] = 4
        self.prepare(keys, 8)
        self.command(bytes([VERSION, ABORT, 8]))
        self.assertEqual(self.command(bytes([VERSION, COMMIT, 8]))["keys"], DEFAULT_KEYS)
