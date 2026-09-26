"""Randy Hub 本機 HTTP 伺服器：Hub UI + JSON API + service 回呼 API（Randy 自有模組）。

安全邊界（取代 x-hub 的 Tauri IPC）：
  - 只綁 127.0.0.1；Host 標頭白名單（防 DNS rebinding）
  - /api/* 需 X-Randy-Hub-Token（每次啟動隨機產生，只經 URL fragment 交給 UI、檔案權限 0600）
    自訂標頭會觸發 CORS 預檢而被拒 → 擴充 iframe（不同 origin）打不進來；另核對 Origin
  - /svc-api/* 只認 service 啟動時發的 Bearer 令牌，且令牌 → 擴充 id 由 Hub 對照（拿不到別人的機密）
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from . import __version__, api
from .content_server import MIME_TYPES, ContentError, resolve_content_path
from .context import build_context
from .paths import HubPaths, hub_paths, write_private

DEFAULT_PORT = 5180
TOKEN_HEADER = "X-Randy-Hub-Token"
MAX_BODY = 1024 * 1024
MAX_CALL_BODY = 96 * 1024 * 1024
STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: http://127.0.0.1:*; "
    "frame-src http://127.0.0.1:*; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; "
    "frame-ancestors 'none'"
)
_SECRET_PATH = re.compile(r"^/svc-api/secrets/([A-Za-z0-9][A-Za-z0-9_.-]{0,63})$")
_ICON_TYPES = {"svg", "png", "jpg", "jpeg", "webp", "ico"}
log = logging.getLogger("randy_hub")


def setup_logging(paths: HubPaths) -> None:
    if any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        return
    handler = RotatingFileHandler(paths.logs / "hub.log", maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


class HubHandler(BaseHTTPRequestHandler):
    server_version = f"RandyHub/{__version__}"
    hub: "HubServer"

    def log_message(self, fmt: str, *args) -> None:
        return

    # ---- 回應 ----
    def _send(self, status: int, body: bytes, mime: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, payload) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str, extra: dict | None = None) -> None:
        self._json(status, {"success": False, "code": code, "message": message, **(extra or {})})

    # ---- 分派 ----
    def do_GET(self) -> None:  # noqa: N802
        self._handle()

    do_POST = do_PUT = do_DELETE = do_HEAD = do_GET  # noqa: N815

    def _handle(self) -> None:
        if self.headers.get("Host", "") not in self.hub.allowed_hosts:
            self._error(403, "BAD_HOST", "Host 不在白名單")
            return
        path = urlsplit(self.path).path
        try:
            if path.startswith("/api/"):
                self._api(path)
            elif path.startswith("/svc-api/"):
                self._svc_api(path)
            else:
                self._static(path)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_body(self, limit: int):
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            raise api.ApiError(400, "INVALID_ARGUMENT", "Content-Length 不合法")
        if length > limit:
            raise api.ApiError(413, "TOO_LARGE", "請求內容太大")
        if length == 0:
            return None
        if "application/json" not in self.headers.get("Content-Type", ""):
            raise api.ApiError(415, "UNSUPPORTED_MEDIA", "只接受 application/json")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise api.ApiError(400, "INVALID_JSON", "JSON 格式錯誤")

    def _api(self, path: str) -> None:
        token = self.headers.get(TOKEN_HEADER, "")
        if not secrets.compare_digest(token, self.hub.token):
            self._error(401, "UNAUTHORIZED", "缺少或錯誤的 Hub 令牌")
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin not in self.hub.allowed_origins:
            self._error(403, "BAD_ORIGIN", "來源不被允許")
            return
        fn, params = api.resolve(self.command, path)
        if not fn:
            self._error(404, "NOT_FOUND", f"沒有這個 API：{self.command} {path}")
            return
        try:
            limit = MAX_CALL_BODY if path.endswith("/call") else MAX_BODY
            body = self._read_body(limit)
            query = dict(parse_qsl(urlsplit(self.path).query))
            self._json(200, fn(self.hub, api.Request(self.command, path, query, body, params)))
        except Exception as exc:  # noqa: BLE001 — 統一轉成友善錯誤，細節進 log
            err = api.translate(exc)
            if err.status >= 500:
                log.exception("API 失敗 %s %s", self.command, path)
            self._error(err.status, err.code, str(err), err.extra)

    def _svc_api(self, path: str) -> None:
        if self.command != "GET" or self.headers.get("Origin") is not None:
            self._error(403, "FORBIDDEN", "service API 只接受後端行程的 GET")
            return
        auth = self.headers.get("Authorization", "")
        ext_id = self.hub.ctx.services.owner_of_token(auth[7:] if auth.startswith("Bearer ") else "")
        if not ext_id:
            self._error(401, "UNAUTHORIZED", "service 令牌無效或服務已停止")
            return
        if path == "/svc-api/whoami":
            self._json(200, {"extId": ext_id})
            return
        match = _SECRET_PATH.match(path)
        if not match:
            self._error(404, "NOT_FOUND", "沒有這個 service API")
            return
        value = self.hub.ctx.ext_secrets(ext_id).get(match.group(1))
        self.hub.ctx.db.audit(ext_id, "service-secret-read", {"name": match.group(1), "found": value is not None})
        if value is None:
            self._error(404, "NOT_FOUND", "此擴充沒有這個機密")
            return
        self._json(200, {"name": match.group(1), "value": value})

    def _static(self, path: str) -> None:
        if self.command not in ("GET", "HEAD"):
            self._error(405, "METHOD_NOT_ALLOWED", "只允許 GET")
            return
        if path in ("/", "/index.html"):
            body = (STATIC_DIR / "index.html").read_bytes()
            self._send(200, body, MIME_TYPES["html"], {"Content-Security-Policy": UI_CSP})
            return
        if path.startswith("/ext-icon/"):
            self._icon(unquote(path[len("/ext-icon/"):]))
            return
        rel = unquote(path[len("/static/"):]) if path.startswith("/static/") else ""
        target = (STATIC_DIR / rel).resolve()
        ext = target.suffix.lower().lstrip(".")
        if not rel or STATIC_DIR not in target.parents or not target.is_file() or ext not in MIME_TYPES:
            self._error(404, "NOT_FOUND", "找不到")
            return
        # Hub origin 下的任何 HTML 都不准被嵌入（防擴充把自己的 iframe 導航到 Hub origin 變成同源）
        extra = {"Content-Security-Policy": UI_CSP} if ext in ("html", "htm") else None
        self._send(200, target.read_bytes(), MIME_TYPES[ext], extra)

    def _icon(self, ext_id: str) -> None:
        loaded = self.hub.ctx.registry.get(ext_id)
        icon = loaded.manifest.icon if loaded else None
        try:  # 每次請求都重驗（開發目錄可能在載入後被換成符號連結）
            target = resolve_content_path(loaded.manifest.root, "/" + icon) if icon else None
        except ContentError:
            target = None
        ext = target.suffix.lower().lstrip(".") if target else ""
        if not target or ext not in _ICON_TYPES:
            self._error(404, "NOT_FOUND", "沒有圖示")
            return
        # SVG 可以帶 <script>：CSP 禁腳本 + sandbox（不透明 origin）+ 禁嵌入，三層都擋
        self._send(200, target.read_bytes(), MIME_TYPES[ext],
                   {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox; frame-ancestors 'none'"})


class HubServer:
    def __init__(self, paths: HubPaths | None = None, port: int = DEFAULT_PORT, secret_backend=None,
                 native_shell: bool = False):
        self.paths = paths or hub_paths()
        setup_logging(self.paths)
        handler = type("BoundHubHandler", (HubHandler,), {"hub": self})
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.allowed_origins = {self.origin, f"http://localhost:{self.port}"}
        self.token = secrets.token_urlsafe(32)
        self.native_shell = native_shell
        self.ctx = build_context(self.paths, self.origin, secret_backend)
        self._thread: threading.Thread | None = None
        self._write_run_info()
        log.info("Randy Hub %s 啟動於 %s（pid %s）", __version__, self.origin, os.getpid())

    @property
    def ui_url(self) -> str:
        return f"{self.origin}/#token={self.token}"

    def _write_run_info(self) -> None:
        info = {"pid": os.getpid(), "port": self.port, "origin": self.origin, "version": __version__}
        write_private(self.paths.run_info, json.dumps(info))
        write_private(self.paths.token_file, self.token)

    def _clear_run_info(self) -> None:
        try:
            if json.loads(self.paths.run_info.read_text(encoding="utf-8")).get("pid") == os.getpid():
                self.paths.run_info.unlink(missing_ok=True)
                self.paths.token_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass

    def start_background(self) -> "HubServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="randy-hub-http", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def shutdown(self) -> None:
        log.info("Randy Hub 關閉中")
        self.ctx.shutdown()
        if self._thread:
            self.httpd.shutdown()
        self.httpd.server_close()
        self._clear_run_info()
