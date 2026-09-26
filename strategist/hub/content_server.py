"""擴充內容伺服器：每個擴充一個獨立 origin（http://127.0.0.1:<專屬埠>）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx):
  - ext_protocol.rs：路徑逐段校驗、拒絕點檔與機密命名/副檔名、內容 CSP、HTML 注入橋腳本
  - market.rs::sensitive_package_file：機密檔名規則
  - docs/adr/0008：擴充內容與宿主資料不同源
Randy 修改（macOS / 無 Tauri 自訂協議）：
  - 以「每擴充專屬埠」取代 `xhub-ext.e-<digest>.localhost` 子網域：不依賴 WKWebView 對 *.localhost 的解析
  - Host 標頭白名單（防 DNS rebinding）、CSP 加 frame-ancestors 只允許 Hub 嵌入
  - 未知副檔名一律 403（x-hub 回 octet-stream）
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlsplit

from .manifest import Manifest

BRIDGE_PATH = "/__randy_hub__/bridge.js"
BRIDGE_TAG = f'<script src="{BRIDGE_PATH}"></script>'
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_SENSITIVE_STEMS = {"credentials", "secrets", "secret", "token", "tokens", "chat_keys", "account_token"}
_SENSITIVE_SUFFIXES = (
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pem", ".key", ".pfx", ".p12", ".env",
    ".xhpack", ".kdbx", ".keychain", ".keychain-db", ".log",
)
MIME_TYPES = {
    "html": "text/html; charset=utf-8", "htm": "text/html; charset=utf-8",
    "js": "text/javascript; charset=utf-8", "mjs": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8", "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "ico": "image/x-icon", "woff": "font/woff",
    "woff2": "font/woff2", "ttf": "font/ttf", "otf": "font/otf", "wasm": "application/wasm",
    "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8", "map": "application/json; charset=utf-8",
}


class ContentError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status


def is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(".") or lower.split(".")[0] in _SENSITIVE_STEMS or lower.endswith(_SENSITIVE_SUFFIXES)


def _segments(url_path: str) -> list[str]:
    parts: list[str] = []
    for raw in url_path.split("/"):
        if raw == "":
            continue
        seg = unquote(raw)
        if seg == ".":
            continue
        if seg == ".." or any(ch in seg for ch in ("\\", "/", ":", "\0")) or any(ord(ch) < 32 for ch in seg):
            raise ContentError(400, "unsafe path segment")
        if is_sensitive_name(seg):
            raise ContentError(403, "sensitive path")
        parts.append(seg)
    if not parts:
        raise ContentError(404, "no file")
    return parts


def resolve_content_path(root: Path, url_path: str) -> Path:
    parts = _segments(url_path)
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if base not in target.parents:
        raise ContentError(403, "escapes extension root")
    if not target.is_file():
        raise ContentError(404, "not found")
    if target.suffix.lower().lstrip(".") not in MIME_TYPES:
        raise ContentError(403, "file type not served")
    return target


def build_csp(network: bool, hub_origin: str) -> str:
    remote = " https: wss:" if network else ""
    return (
        "default-src 'none'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob:{remote}; font-src 'self' data:; connect-src 'self'{remote}; "
        f"media-src 'self' blob:{remote}; frame-src 'none'; object-src 'none'; base-uri 'self'; "
        f"form-action 'none'; worker-src 'self' blob:; frame-ancestors {hub_origin}"
    )


def inject_bridge(html: str, tag: str = BRIDGE_TAG) -> str:
    """優先插在 <head ...> 之後（早於擴充自身腳本）；否則 </head> 前；否則 <body 前；都沒有就放最前。"""
    lower = html.lower()
    start = lower.find("<head")
    if start >= 0:
        close = lower.find(">", start)
        if close >= 0:
            return html[: close + 1] + tag + html[close + 1 :]
    for marker in ("</head>", "<body"):
        pos = lower.find(marker)
        if pos >= 0:
            return html[:pos] + tag + html[pos:]
    return tag + html


def render_bridge(hub_origin: str) -> bytes:
    source = (_STATIC_DIR / "xhub-bridge.js").read_text(encoding="utf-8")
    return source.replace("__RANDY_HUB_ORIGIN__", hub_origin).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "RandyHubExt/0.1"
    owner: "ExtensionContentServer"

    def log_message(self, fmt: str, *args) -> None:  # 安靜：內容請求很多
        return

    def _deny(self, status: int, reason: str) -> None:
        body = reason.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _send(self, body: bytes, mime: str, *, html: bool) -> None:
        owner = self.owner
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if html:
            self.send_header("Content-Security-Policy", build_csp(owner.network_allowed(), owner.hub_origin))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_ok():
            self._deny(403, "bad host")
            return
        if self.headers.get("Service-Worker"):  # 瀏覽器抓 service worker 腳本時才帶；拒絕 = 無法註冊常駐攔截腳本
            self._deny(403, "service workers are not allowed")
            return
        path = urlsplit(self.path).path
        if path == BRIDGE_PATH:
            self._send(self.owner.bridge_js, "text/javascript; charset=utf-8", html=False)
            return
        manifest = self.owner.manifest()
        if manifest is None or manifest.disabled_reason:
            self._deny(404, "extension unavailable")
            return
        try:
            target = resolve_content_path(manifest.root, path)
        except ContentError as exc:
            self._deny(exc.status, str(exc))
            return
        ext = target.suffix.lower().lstrip(".")
        body = target.read_bytes()
        if ext in ("html", "htm"):
            body = inject_bridge(body.decode("utf-8", errors="replace")).encode("utf-8")
        self._send(body, MIME_TYPES[ext], html=ext in ("html", "htm"))

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _method_not_allowed(self) -> None:
        self._deny(405, "method not allowed")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _method_not_allowed  # noqa: N815


class ExtensionContentServer:
    """一個擴充一台：只服務該擴充目錄內的公開靜態檔。"""

    def __init__(self, manifest: Callable[[], Manifest | None], hub_origin: str,
                 network_allowed: Callable[[], bool], port: int = 0, avoid_ports: set[int] | None = None):
        self.manifest = manifest
        self.hub_origin = hub_origin
        self.network_allowed = network_allowed
        self.bridge_js = render_bridge(hub_origin)
        self._preferred_port = port
        self._avoid = set(avoid_ports or ()) - {port}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else 0

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _bind(self) -> ThreadingHTTPServer:
        handler = type("BoundHandler", (_Handler,), {"owner": self})
        if self._preferred_port:
            try:
                return ThreadingHTTPServer(("127.0.0.1", self._preferred_port), handler)
            except OSError:
                pass  # 固定埠被占就退回動態埠
        for _ in range(32):  # 動態埠若撞到別的擴充身分用過的埠（= 別人的 origin）就重抽
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            if httpd.server_address[1] not in self._avoid:
                return httpd
            httpd.server_close()
        raise OSError("找不到可用的擴充內容埠")

    def start(self) -> "ExtensionContentServer":
        self._httpd = self._bind()
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name=f"ext-content-{self.port}", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
