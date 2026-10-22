"""本机 Web 配置服务。仅绑定回环地址，静态页面和 BLE 都由本项目提供。"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import logging
import secrets
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from keyboard_app import KeyboardApp
from keymap_protocol import DEFAULT_KEYS

ROOT = Path(__file__).resolve().parent


class BleWorker:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.call(self._init())

    async def _init(self):
        self.client = KeyboardApp(ROOT / "runtime")
        self.client.reconnect = asyncio.create_task(self.client.maintain())

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=40)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("操作超时，请检查键盘电源和蓝牙连接。") from None

    def close(self):
        try:
            self.call(self.client.close())
        except Exception:
            logging.exception("Disconnect during shutdown")
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


class ConfigServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8787, worker=None):
        self.token = secrets.token_urlsafe(24)
        self.worker = worker or BleWorker()
        try:
            super().__init__(("127.0.0.1", port), Handler)
        except BaseException:
            if worker is None:
                self.worker.close()
            raise


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logging.info(fmt, *args)

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        port = self.server.server_port
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in hosts:
            self.reply(403, {"error": "请从本机配置页打开服务。"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + host for host in hosts}:
            self.reply(403, {"error": "请求来源无效。"})
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        route = urlsplit(self.path).path
        try:
            if route == "/api/session":
                self.reply(
                    200,
                    {"app": "ibook-keymap", "token": self.server.token, "defaults": DEFAULT_KEYS},
                )
            elif route == "/api/status":
                self.reply(200, self.server.worker.call(self.server.worker.client.status()))
            elif route == "/api/devices":
                self.reply(
                    200, {"devices": self.server.worker.call(self.server.worker.client.devices())}
                )
            else:
                assets = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                    "/style.css": ("style.css", "text/css; charset=utf-8"),
                    "/model.js": ("model.js", "text/javascript; charset=utf-8"),
                    "/voice.js": ("voice.js", "text/javascript; charset=utf-8"),
                }
                if route not in assets:
                    self.reply(404, {"error": "页面不存在。"})
                    return
                name, content_type = assets[route]
                body = (ROOT / "web" / name).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except Exception as exc:
            logging.exception("GET %s", route)
            self.reply(503, {"error": friendly(exc)})

    def do_POST(self):
        # 拒绝请求前读完有限长度的正文，避免 Windows 因未读正文关闭 TCP 时
        # 发出复位，导致浏览器收不到应返回的 403。
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 4096:
                raise ValueError("请求数据过长。")
            self.connection.settimeout(5)
            body = self.rfile.read(length)
        except (ValueError, OSError) as exc:
            self.reply(400, {"error": str(exc)})
            return
        if not self.allowed():
            return
        if not secrets.compare_digest(
            self.headers.get("X-Keyboard-Token", "").encode("utf-8"),
            self.server.token.encode("ascii"),
        ):
            self.reply(403, {"error": "页面已过期，请刷新后重试。"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求格式无效。")
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("请求格式无效。")
            route = urlsplit(self.path).path
            client, worker = self.server.worker.client, self.server.worker
            if route == "/api/connect":
                result = worker.call(client.connect(data.get("address")))
            elif route == "/api/disconnect":
                worker.call(client.disconnect())
                result = {"connected": False}
            elif route == "/api/config":
                result = worker.call(client.save(data.get("keys")))
            elif route == "/api/sleep":
                result = worker.call(client.save_sleep(data.get("minutes")))
            elif route == "/api/rotary":
                result = worker.call(client.save_rotary(data.get("mode")))
            elif route == "/api/voice":
                result = worker.call(client.configure_voice(data))
            elif route == "/api/read":
                result = worker.call(client.read())
            elif route == "/api/stop":
                self.reply(200, {"stopped": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                self.reply(404, {"error": "接口不存在。"})
                return
            self.reply(200, result)
        except (ValueError, UnicodeError) as exc:
            self.reply(400, {"error": str(exc)})
        except (ConnectionError, TimeoutError) as exc:
            self.reply(503, {"error": friendly(exc)})
        except Exception as exc:
            logging.exception("POST %s", self.path)
            self.reply(503, {"error": friendly(exc)})


def friendly(exc):
    if isinstance(exc, (ValueError, ConnectionError, TimeoutError)):
        return str(exc)
    return "蓝牙操作未完成。请确认键盘已配对且有电，再重新连接；未保存的草稿仍在页面中。"


def main():
    parser = argparse.ArgumentParser(description="ibook Keyboard 本地按键配置页")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    parser.add_argument("--status", action="store_true", help="查看统一工作台状态")
    parser.add_argument("--stop", action="store_true", help="停止统一工作台和语音")
    args = parser.parse_args()
    log = ROOT / "runtime" / "web-config.log"
    log.parent.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=log, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    url = f"http://127.0.0.1:{args.port}"
    if args.status or args.stop:
        try:
            with urllib.request.urlopen(url + "/api/session", timeout=3) as response:
                session = json.load(response)
            if session.get("app") != "ibook-keymap":
                raise ValueError("端口由其他程序使用")
            if args.stop:
                request = urllib.request.Request(
                    url + "/api/stop",
                    data=b"{}",
                    headers={
                        "Content-Type": "application/json",
                        "X-Keyboard-Token": session["token"],
                    },
                )
            else:
                request = url + "/api/status"
            with urllib.request.urlopen(request, timeout=5) as response:
                print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
        except (urllib.error.URLError, ValueError):
            print("工作台未运行。请双击 pc/start.cmd。")
        return
    try:
        server = ConfigServer(args.port)
    except OSError:
        try:
            with urllib.request.urlopen(url + "/api/session", timeout=2) as response:
                existing = json.load(response)
            if existing.get("app") != "ibook-keymap":
                raise RuntimeError("端口已被其他程序占用。") from None
            if args.open:
                webbrowser.open(url)
            return
        except (urllib.error.URLError, ValueError):
            raise RuntimeError("配置服务启动失败，请查看 pc/runtime/web-config.log。") from None
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.worker.close()


if __name__ == "__main__":
    main()
