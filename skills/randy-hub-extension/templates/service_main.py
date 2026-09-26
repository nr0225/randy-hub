"""Randy Hub service 擴充後端骨架（只用 Python 標準庫）。

改寫自 x-hub templates/service.index.js（MIT, © 2026 dckxx）：Node → Python，加上向 Hub 取「自己的」機密。
"""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "0"))


def own_secret(name):
    """向 Hub 取本擴充自己的機密；令牌決定身分，拿不到別人的。找不到回 None。"""
    req = urllib.request.Request(
        f"{os.environ['RANDY_HUB_URL']}/svc-api/secrets/{name}",
        headers={"Authorization": f"Bearer {os.environ['RANDY_HUB_SERVICE_TOKEN']}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8")).get("value")
    except urllib.error.HTTPError:
        return None


class Handler(BaseHTTPRequestHandler):
    def _json(self, body, status=200):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            self._json({"ok": True})
        elif self.path == "/api/hello":
            self._json({"message": "hi", "ext": os.environ.get("XHUB_EXT_ID")})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)  # 落盤到 logs/service/<擴充 id>.log


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()  # 只聽回環
