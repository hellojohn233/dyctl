"""测试用的本地 HTTP 服务器。

之前这两个测试脚本依赖 httpbin.org，那个站点时好时坏（直连和走代理都超时），
测试红着跟代码没关系，纯属外部依赖不可控。改成本地起一个随机端口的服务，
测试就完全不依赖外网了。

提供三个端点：
- GET  /json   回显请求头和路径（JSON）
- GET  /form   一个 HTML 表单
- POST /post   回显提交上来的表单字段（JSON）
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl

FORM_HTML = """<!doctype html>
<html>
  <head><title>order</title></head>
  <body>
    <form method="post" action="/post">
      <input name="custname" type="text">
      <input name="custtel" type="tel">
      <input name="size" type="radio" value="small">
      <input name="size" type="radio" value="medium">
      <input name="size" type="radio" value="large">
      <select name="topping">
        <option value="cheese">cheese</option>
        <option value="mushroom">mushroom</option>
      </select>
      <button>Submit order</button>
    </form>
  </body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # 静音，别刷屏
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/json"):
            payload = {
                "method": "GET",
                "url": self.path,
                "headers": dict(self.headers),
                "args": dict(parse_qsl(self.path.partition("?")[2])),
            }
            self._send(200, json.dumps(payload).encode(), "application/json")
            return
        if self.path.startswith("/form"):
            self._send(200, FORM_HTML.encode())
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        payload = {
            "method": "POST",
            "url": self.path,
            "headers": dict(self.headers),
            "data": raw,
            "form": dict(parse_qsl(raw)),
        }
        self._send(200, json.dumps(payload).encode(), "application/json")


def start_server() -> tuple[str, ThreadingHTTPServer]:
    """起一个本地服务，返回 (base_url, server)。

    调用方负责 server.shutdown()。
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return f"http://{host}:{port}", server
