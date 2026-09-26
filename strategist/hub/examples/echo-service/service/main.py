"""Echo 後端（Randy Hub service 擴充範例，只用標準庫）。

宿主注入的環境變數：PORT、XHUB_EXT_ID、RANDY_HUB_URL、RANDY_HUB_SERVICE_TOKEN。
只聽 127.0.0.1；機密透過 Hub 的 /svc-api 取回（令牌決定身分，拿不到別的擴充的機密）。
"""
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PORT = int(os.environ.get("PORT", "0"))
HUB = os.environ.get("RANDY_HUB_URL", "")
TOKEN = os.environ.get("RANDY_HUB_SERVICE_TOKEN", "")


def hub_get(path):
    req = urllib.request.Request(HUB + path, headers={"Authorization": f"Bearer {TOKEN}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, {}


class Handler(BaseHTTPRequestHandler):
    def _json(self, body, status=200):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        url = urlsplit(self.path)
        if url.path == "/healthz":
            self._json({"ok": True})
        elif url.path == "/api/echo":
            msg = parse_qs(url.query).get("msg", [""])[0]
            self._json({"echo": msg, "ext": os.environ.get("XHUB_EXT_ID"), "pid": os.getpid()})
        elif url.path == "/api/whoami":
            status, body = hub_get("/svc-api/whoami")
            self._json({"status": status, "hubSaysIAm": body.get("extId")})
        elif url.path == "/api/secret-check":
            status, body = hub_get("/svc-api/secrets/DEMO_TOKEN")
            value = body.get("value") or ""
            self._json({"found": status == 200, "length": len(value)})  # 只回長度，不回值
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        print("[echo-service]", fmt % args, flush=True)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"echo-service listening on 127.0.0.1:{server.server_address[1]}", flush=True)
    server.serve_forever()
