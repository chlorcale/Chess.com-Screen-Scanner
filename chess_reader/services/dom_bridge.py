from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class _Handler(BaseHTTPRequestHandler):
    server_version = "ChessDOMBridge/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        body = b'{"ok":true,"service":"chess-dom-bridge"}'
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path != "/moves":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
            callback = self.server.callback  # type: ignore[attr-defined]
            callback(payload)
            body = b'{"ok":true}'
            self.send_response(200)
        except Exception as exc:
            body = json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(400)

        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


class DomBridgeService:
    def __init__(self, host: str, port: int, on_payload: Callable[[dict], None]):
        self.host = host
        self.port = port
        self.on_payload = on_payload
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self):
        if self._server is not None:
            return

        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.callback = self.on_payload  # type: ignore[attr-defined]
        self._server = server

        self._thread = threading.Thread(
            target=server.serve_forever,
            name="dom-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        server = self._server
        self._server = None
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()

    def address(self) -> str:
        return f"http://{self.host}:{self.port}"
