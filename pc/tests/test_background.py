import argparse
import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_background as bg
import voice_discovery as discovery
from tools import background as launcher
from voice_protocol import SERVICE_UUID


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def args(self, folder):
        return argparse.Namespace(
            address=None, name="ibook Keyboard", state_dir=Path(folder), ignore_cache=False
        )

    async def test_verified_cache_avoids_scanning(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(folder)
            device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
            discovery.remember_device(args, device)
            scanner = mock.AsyncMock()
            with (
                mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakScanner=scanner)}),
                mock.patch.object(discovery, "known_device", return_value=device),
                mock.patch.object(discovery, "paired_windows_devices", mock.AsyncMock()) as paired,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertIs(await discovery.discover_device(args), device)
            scanner.discover.assert_not_called()
            paired.assert_not_called()

    async def test_cache_failure_refresh_uses_already_paired_device(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(folder)
            args.ignore_cache = True
            old = SimpleNamespace(address="00:00:00:00:00:01")
            actual = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
            discovery.remember_device(args, old)
            scanner = mock.AsyncMock()
            with (
                mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakScanner=scanner)}),
                mock.patch.object(
                    discovery, "paired_windows_devices", mock.AsyncMock(return_value=[actual])
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertIs(await discovery.discover_device(args), actual)
            scanner.discover.assert_not_called()

    async def test_scan_prioritizes_voice_service_and_does_not_cache_unverified_peer(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.args(folder)
            wrong = SimpleNamespace(address="00:00:00:00:00:01", name="ibook Keyboard")
            actual = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="ibook Keyboard")
            scanner = SimpleNamespace(
                discover=mock.AsyncMock(
                    return_value={
                        wrong.address: (
                            wrong,
                            SimpleNamespace(service_uuids=[], local_name="ibook Keyboard"),
                        ),
                        actual.address: (
                            actual,
                            SimpleNamespace(
                                service_uuids=[SERVICE_UUID.upper()], local_name="ibook Keyboard"
                            ),
                        ),
                    }
                )
            )
            with (
                mock.patch.dict(sys.modules, {"bleak": SimpleNamespace(BleakScanner=scanner)}),
                mock.patch.object(
                    discovery, "paired_windows_devices", mock.AsyncMock(return_value=[])
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertIs(await discovery.discover_device(args), actual)
            self.assertFalse((Path(folder) / "device.json").exists())

    def test_multiple_keyboards_require_selection_but_duplicate_ids_are_safe(self):
        first = SimpleNamespace(address="AA:BB:CC:DD:EE:FF")
        second = SimpleNamespace(address="AA:BB:CC:DD:EE:01")
        self.assertIs(discovery.choose([first, first]), first)
        with self.assertRaisesRegex(RuntimeError, "Multiple"):
            discovery.choose([first, second])

    def test_corrupted_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "device.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(discovery.read_json(path), {})


class BackgroundTests(unittest.IsolatedAsyncioTestCase):
    def test_stop_requests_current_instance_and_waits_for_exit(self):
        folder = Path("unused-state-folder")
        with (
            mock.patch.object(launcher, "is_running", side_effect=[True, False]),
            mock.patch.object(launcher, "read_json", return_value={"token": "current-instance"}),
            mock.patch.object(launcher, "write_json") as write,
            mock.patch.object(launcher.time, "sleep") as sleep,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            launcher.stop_receiver(folder)
        write.assert_called_once_with(folder / "stop.json", {"token": "current-instance"})
        sleep.assert_not_called()
        self.assertIn("stopped", output.getvalue())

    @unittest.skipUnless(sys.platform == "win32", "Windows background launcher")
    def test_stopped_process_does_not_report_stale_connected_state(self):
        with (
            mock.patch.object(launcher, "is_running", return_value=False),
            mock.patch.object(
                launcher, "read_json", return_value={"connected": True, "status": "complete"}
            ),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(launcher.main(["status"]), 0)
        state = json.loads(output.getvalue())
        self.assertFalse(state["running"])
        self.assertFalse(state["connected"])
        self.assertEqual(state["status"], "stopped")

    def test_stop_request_is_bound_to_current_process_token(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = bg.Runtime(Path(folder))
            discovery.write_json(Path(folder) / "stop.json", {"token": "stale"})
            self.assertFalse(runtime.stop_requested())
            discovery.write_json(Path(folder) / "stop.json", {"token": runtime.value["token"]})
            self.assertTrue(runtime.stop_requested())

    @unittest.skipUnless(sys.platform == "win32", "Windows named mutex")
    def test_duplicate_instances_are_prevented(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.assertFalse(bg.is_running(path))
            lock = bg.SingleInstance(path)
            try:
                self.assertTrue(bg.is_running(path))
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    bg.SingleInstance(path)
            finally:
                lock.close()
            self.assertFalse(bg.is_running(path))

    async def test_disconnect_reconnect_then_graceful_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = bg.Runtime(Path(folder))
            args = argparse.Namespace(runtime=runtime, retry_delay=0.01, ignore_cache=False)
            calls = 0
            cleaned = False

            async def receive(args, output):
                nonlocal calls, cleaned
                calls += 1
                if calls == 1:
                    raise RuntimeError("test disconnect")
                runtime.update(status="connected", connected=True, last_error=None)
                discovery.write_json(Path(folder) / "stop.json", {"token": runtime.value["token"]})
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned = True

            with contextlib.redirect_stdout(io.StringIO()):
                await asyncio.wait_for(bg.run_daemon(args, None, receive), 3)
            self.assertEqual(calls, 2)
            self.assertTrue(args.ignore_cache)
            self.assertTrue(cleaned)
            self.assertEqual(runtime.value["status"], "stopped")
            self.assertFalse(runtime.value["connected"])


if __name__ == "__main__":
    unittest.main()
