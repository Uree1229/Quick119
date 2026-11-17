"""Local-only headless Chrome test helper using CDP; no third-party packages.

Uses a new temporary profile. The debugging port is bound to loopback only.
"""
import base64
import http.client
import json
import os
import socket
import struct
import subprocess
import time
from urllib.parse import urlsplit


class Chrome:
    def __init__(self, executable, profile):
        self.profile = profile
        self.sequence = 0
        self.connection = None
        self.process = subprocess.Popen([
            str(executable), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-background-networking", "--disable-sync",
            "--disable-component-update", "--disable-extensions", "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1", "--user-data-dir=" + str(profile), "about:blank"
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            active = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 10
            port = None
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError("Chrome exited before debugging started")
                try:
                    port = int(active.read_text().splitlines()[0])
                    break
                except (OSError, ValueError, IndexError):
                    pass  # Chrome may still hold its newly-created readiness file open on Windows.
                time.sleep(.05)
            if port is None:
                raise RuntimeError("Chrome debugging readiness timed out")
            http_connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                http_connection.request("GET", "/json/list")
                targets = json.loads(http_connection.getresponse().read())
            finally:
                http_connection.close()
            target = next(target for target in targets if target["type"] == "page")
            url = urlsplit(target["webSocketDebuggerUrl"])
            if url.hostname not in {"127.0.0.1", "localhost"} or url.port != port:
                raise RuntimeError("Non-loopback debugging endpoint rejected")
            self.connection = socket.create_connection(("127.0.0.1", port), timeout=25)
            key = base64.b64encode(os.urandom(16)).decode()
            request = (f"GET {url.path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
            self.connection.sendall(request.encode())
            header = b""
            while not header.endswith(b"\r\n\r\n"):
                header += self._read(1)
            if not header.startswith(b"HTTP/1.1 101"):
                raise RuntimeError("WebSocket upgrade failed")
        except Exception:
            self.close()
            raise

    def _read(self, count):
        value = b""
        while len(value) < count:
            part = self.connection.recv(count - len(value))
            if not part:
                raise ConnectionError("Chrome debugging connection closed")
            value += part
        return value

    def _send(self, payload):
        mask = os.urandom(4)
        length = len(payload)
        header = bytes([0x81, 0x80 | length]) if length < 126 else bytes([0x81, 0xFE]) + struct.pack(">H", length)
        self.connection.sendall(header + mask + bytes(value ^ mask[index % 4] for index, value in enumerate(payload)))

    def call(self, method, **params):
        self.sequence += 1
        message_id = self.sequence
        self._send(json.dumps({"id": message_id, "method": method, "params": params}).encode())
        while True:
            first, second = self._read(2)
            length = second & 127
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            if second & 128:
                raise RuntimeError("Unexpected masked server frame")
            payload = self._read(length)
            if first & 15 == 8:
                raise ConnectionError("Chrome closed WebSocket")
            if first & 15 != 1:
                continue
            message = json.loads(payload)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result", {})

    def close(self):
        if self.connection:
            try:
                self._send(json.dumps({"id": 99999, "method": "Browser.close"}).encode())
            except OSError:
                pass
            self.connection.close()
            self.connection = None
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=5)
