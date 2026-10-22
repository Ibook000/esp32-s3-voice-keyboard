from __future__ import annotations

import asyncio
import copy
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from keyboard_app import DEFAULT_VOICE, validate_voice
from keymap_protocol import DEFAULT_KEYS, validate_keys
from web_config import ConfigServer


class FakeClient:
    def __init__(self):
        self.connected = False
        self.keys = copy.deepcopy(DEFAULT_KEYS)
        self.voice = dict(DEFAULT_VOICE)

    async def status(self):
        return {
            "connected": self.connected,
            "config": {"keys": self.keys} if self.connected else None,
        }

    async def save_sleep(self, minutes):
        if type(minutes) is not int or not 0 <= minutes <= 120:
            raise ValueError("Invalid sleep timeout")
        return {"sleep_minutes": minutes}

    async def save_rotary(self, mode):
        if mode not in ("scroll", "volume"):
            raise ValueError("Invalid mode")
        return {"rotary_mode": mode}

    async def configure_voice(self, options):
        self.voice = validate_voice(options)
        return {"settings": self.voice}

    async def devices(self):
        return [{"address": "44:1B:F6:FF:C9:1D", "name": "ibook Keyboard"}]

    async def connect(self, address):
        self.connected = True
        return {"keys": self.keys}

    async def disconnect(self):
        self.connected = False

    async def read(self):
        return {"keys": self.keys}

    async def save(self, keys):
        if not self.connected:
            raise ConnectionError("键盘已断开。")
        self.keys = validate_keys(keys)
        return {"keys": self.keys, "revision": 1}


class FakeWorker:
    def __init__(self):
        self.client = FakeClient()

    def call(self, coro):
        return asyncio.run(coro)


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.server = ConfigServer(0, FakeWorker())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", data=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {
            "Content-Type": "application/json",
            "X-Keyboard-Token": self.server.token,
        }
        request_headers.update(headers or {})
        connection.request(
            method,
            path,
            body=json.dumps(data).encode() if data is not None else None,
            headers=request_headers,
        )
        response = connection.getresponse()
        body = response.read()
        result = (response.status, dict(response.getheaders()), body)
        connection.close()
        return result

    def test_local_assets_and_no_path_traversal(self):
        for path in ["/", "/app.js", "/style.css", "/model.js"]:
            status, headers, body = self.request(path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(self.request("/../web_config.py")[0], 404)

    def test_connect_load_save_and_read_back(self):
        self.assertEqual(self.request("/api/config", "POST", {"keys": DEFAULT_KEYS})[0], 503)
        self.assertEqual(
            self.request("/api/connect", "POST", {"address": "44:1B:F6:FF:C9:1D"})[0], 200
        )
        keys = copy.deepcopy(DEFAULT_KEYS)
        keys[2].update(label="保存", key=22)
        status, _, body = self.request("/api/config", "POST", {"keys": keys})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["keys"], keys)
        self.assertEqual(json.loads(self.request("/api/read", "POST", {})[2])["keys"], keys)

    def test_token_origin_and_host_checks(self):
        self.assertEqual(
            self.request("/api/connect", "POST", {}, {"X-Keyboard-Token": "wrong"})[0], 403
        )
        self.assertEqual(self.request("/api/session", headers={"Host": "evil.test"})[0], 403)
        self.assertEqual(
            self.request("/api/config", "POST", {}, {"Origin": "https://evil.test"})[0], 403
        )

    def test_validation_and_unknown_routes(self):
        self.request("/api/connect", "POST", {"address": "44:1B:F6:FF:C9:1D"})
        self.assertEqual(self.request("/api/config", "POST", {"keys": []})[0], 400)
        self.assertEqual(self.request("/api/missing", "POST", {})[0], 404)

    def test_voice_settings_share_api_security_and_validation(self):
        options = dict(DEFAULT_VOICE, enabled=True, type_text=False)
        status, _, body = self.request("/api/voice", "POST", options)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["settings"], options)
        self.assertEqual(
            self.request("/api/voice", "POST", {}, {"X-Keyboard-Token": "bad"})[0], 403
        )
        self.assertEqual(
            self.request("/api/voice", "POST", dict(options, model="../other"))[0], 400
        )

    def test_rotary_mode_route(self):
        code, _, body = self.request("/api/rotary", "POST", {"mode": "volume"})
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["rotary_mode"], "volume")
        self.assertEqual(self.request("/api/rotary", "POST", {"mode": "other"})[0], 400)

    def test_sleep_route_range_and_token(self):
        for minutes in (0, 1, 10, 120):
            code, _, body = self.request("/api/sleep", "POST", {"minutes": minutes})
            self.assertEqual(code, 200)
            self.assertEqual(json.loads(body)["sleep_minutes"], minutes)
        for minutes in (-1, 121, True, "10"):
            self.assertEqual(self.request("/api/sleep", "POST", {"minutes": minutes})[0], 400)
        self.assertEqual(
            self.request("/api/sleep", "POST", {"minutes": 1}, {"X-Keyboard-Token": "wrong"})[0],
            403,
        )
