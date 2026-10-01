"""service 擴充的背景行程托管（background service runtime）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — service.rs / runtime.rs / docs/adr/0007：
  - 動態埠 PORT、只聽回環、health 探活、stdout/stderr 落盤（單份 1MB、每次啟動寫分隔線）
  - 子行程只繼承執行必要的環境變數（不繼承雲端憑證、NODE_OPTIONS 等）
  - 預設不啟動：使用者必須信任「目前版本」；撤銷即停止並作廢令牌
Randy 修改：
  - 不自動下載執行期（x-hub 會下載內建 Node）：只用本機 node / Hub 自己的 Python，版本不夠就拒絕並說清楚
  - engine=python（Randy 生態），以 -E -s -B 啟動（忽略 PYTHON* 環境變數與使用者 site-packages）
  - 服務回呼 Hub 取「自己的」機密：每次啟動發一枚隨機令牌，令牌 → 擴充 id 由 Hub 對照，無法冒名
  - 前端呼叫後端一律經 Hub 代轉（bridge service.request），不開 /svc 反向代理
"""
from __future__ import annotations

import http.client
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .extensions import ExtensionRegistry
from .paths import HubPaths

LOG_LIMIT = 1024 * 1024
READY_TIMEOUT_S = 15.0
MAX_BODY = 8 * 1024 * 1024
BASE_ENV_KEYS = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ", "SHELL")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_HOP_HEADERS = {"host", "connection", "content-length", "transfer-encoding", "keep-alive", "upgrade", "proxy-authorization"}


class ServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class ServiceProcess:
    ext_id: str
    version: str
    proc: subprocess.Popen
    port: int
    host: str
    token: str
    log_path: Path
    started_at: float = field(default_factory=time.time)
    ready: bool = False


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _connect_host(host: str) -> str:
    """Turn wildcard listen addresses into a safe local connect target."""
    if host in ("", "0.0.0.0"):
        return "127.0.0.1"
    if host == "::":
        return "::1"
    return host


def build_service_env(ext_id: str, port: int, host: str, token: str, hub_url: str, engine_dir: str) -> dict:
    """白名單式組環境變數：沒列在這裡的（API key、雲端憑證、NODE_OPTIONS、PYTHONPATH…）一律不給。"""
    env = {key: os.environ[key] for key in BASE_ENV_KEYS if key in os.environ}
    env["PATH"] = f"{engine_dir}:{SYSTEM_PATH}" if engine_dir else SYSTEM_PATH
    env.update({
        "PORT": str(port), "XHUB_EXT_ID": ext_id, "XHUB_LISTEN_HOST": host,
        "RANDY_HUB_EXT_ID": ext_id, "RANDY_HUB_URL": hub_url, "RANDY_HUB_SERVICE_TOKEN": token,
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    })
    return env


def parse_major(version_text: str) -> int | None:
    match = re.search(r"(\d+)", version_text or "")
    return int(match.group(1)) if match else None


def node_command(entry: Path, min_version: str) -> list[str]:
    node = shutil.which("node")
    if not node:
        raise ServiceError("RUNTIME_MISSING", "找不到 node：請先安裝 Node.js（Hub 不會自動下載執行期）")
    output = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    have, need = parse_major(output), parse_major(min_version)
    if need and (have or 0) < need:
        raise ServiceError("RUNTIME_TOO_OLD", f"擴充要求 Node ≥ {need}，本機是 {output or '未知版本'}")
    return [node, str(entry)]


def python_command(entry: Path) -> list[str]:
    return [sys.executable, "-E", "-s", "-B", str(entry)]


def _open_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > LOG_LIMIT:
        path.replace(path.with_suffix(".log.1"))
    handle = open(path, "a", encoding="utf-8")
    handle.write(f"----- service 啟動 {time.strftime('%Y-%m-%d %H:%M:%S')} -----\n")
    handle.flush()
    return handle


def _tail(path: Path, lines: int = 20) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


class ServiceManager:
    def __init__(self, paths: HubPaths, registry: ExtensionRegistry, hub_url: Callable[[], str]):
        self.paths = paths
        self.registry = registry
        self._hub_url = hub_url
        self._procs: dict[str, ServiceProcess] = {}
        self._tokens: dict[str, str] = {}
        self._lock = threading.RLock()

    # ---- 啟停 ----
    def _command(self, manifest) -> list[str]:
        backend = manifest.backend
        entry = manifest.root / backend.entry
        if backend.engine_type == "python":
            return python_command(entry)
        return node_command(entry, backend.min_version)

    def _check_startable(self, ext_id: str):
        loaded = self.registry.get(ext_id)
        if not loaded or loaded.manifest.runtime != "service" or not loaded.manifest.backend:
            raise ServiceError("NOT_FOUND", f"{ext_id} 不是 service 擴充")
        if loaded.manifest.disabled_reason:
            raise ServiceError("DISABLED", loaded.manifest.disabled_reason)
        if not self.registry.is_trusted(ext_id):
            raise ServiceError("NOT_TRUSTED", "後端預設不啟動：請先在擴充設定中信任這個版本")
        backend = loaded.manifest.backend
        if backend.host not in ("127.0.0.1", "::1", "localhost") and not self.registry.is_granted(ext_id, "network"):
            raise ServiceError("PERMISSION_DENIED", "對外監聽需要使用者授權 network 權限")
        return loaded

    def start(self, ext_id: str) -> dict:
        with self._lock:
            if self._alive(ext_id):
                return self.status(ext_id)
            loaded = self._check_startable(ext_id)
            manifest, backend = loaded.manifest, loaded.manifest.backend
            command = self._command(manifest)
            if backend.port and not port_is_free(backend.port):  # 否則探活會被別的伺服器（例如軍師閘道）滿足
                raise ServiceError("PORT_IN_USE", f"固定埠 {backend.port} 已被占用；請把 backend.port 改成 0（動態分配）")
            port = backend.port or free_port()
            token = secrets.token_urlsafe(32)
            log_path = self.paths.service_logs / f"{ext_id}.log"
            env = build_service_env(ext_id, port, backend.host, token, self._hub_url(), str(Path(command[0]).parent))
            with _open_log(log_path) as log:
                proc = subprocess.Popen(command, cwd=str(manifest.root / backend.cwd), env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            self._procs[ext_id] = ServiceProcess(ext_id, manifest.version, proc, port, backend.host, token, log_path)
            self._tokens[token] = ext_id
        self.registry.db.audit("hub", "service-start", {"ext": ext_id, "port": port, "pid": proc.pid})
        self._wait_ready(ext_id, backend.health or "/")
        return self.status(ext_id)

    def _wait_ready(self, ext_id: str, health: str) -> None:
        deadline = time.time() + READY_TIMEOUT_S
        while time.time() < deadline:
            svc = self._procs.get(ext_id)
            if not svc:
                return
            if svc.proc.poll() is not None:
                self._forget(ext_id)
                raise ServiceError("SERVICE_EXITED", f"後端啟動後結束（exit={svc.proc.returncode}）\n{_tail(svc.log_path)}")
            try:
                conn = http.client.HTTPConnection(_connect_host(svc.host), svc.port, timeout=1)
                conn.request("GET", health)
                status = conn.getresponse().status
                conn.close()
                if status < 500:
                    svc.ready = True
                    return
            except OSError:
                pass
            time.sleep(0.2)
        svc = self._procs.get(ext_id)
        log_path = svc.log_path if svc else self.paths.service_logs / f"{ext_id}.log"
        self.stop(ext_id)
        raise ServiceError("SERVICE_NOT_READY", f"後端 {READY_TIMEOUT_S:.0f} 秒內沒有回應 {health}；日誌：{log_path}")

    def _forget(self, ext_id: str) -> ServiceProcess | None:
        with self._lock:
            svc = self._procs.pop(ext_id, None)
            if svc:
                self._tokens.pop(svc.token, None)
            return svc

    def stop(self, ext_id: str) -> bool:
        svc = self._forget(ext_id)
        if not svc:
            return False
        if svc.proc.poll() is None:
            try:
                os.killpg(svc.proc.pid, signal.SIGTERM)
                svc.proc.wait(timeout=3)
            except (ProcessLookupError, PermissionError):
                pass
            except subprocess.TimeoutExpired:
                os.killpg(svc.proc.pid, signal.SIGKILL)
                svc.proc.wait(timeout=3)
        self.registry.db.audit("hub", "service-stop", {"ext": ext_id})
        return True

    def stop_all(self) -> None:
        for ext_id in list(self._procs):
            self.stop(ext_id)

    # ---- 狀態 / 令牌 ----
    def _alive(self, ext_id: str) -> bool:
        svc = self._procs.get(ext_id)
        return bool(svc) and svc.proc.poll() is None

    def status(self, ext_id: str) -> dict:
        svc = self._procs.get(ext_id)
        log_path = self.paths.service_logs / f"{ext_id}.log"
        if not svc:
            return {"running": False, "ready": False, "logPath": str(log_path)}
        code = svc.proc.poll()
        return {"running": code is None, "ready": svc.ready and code is None, "pid": svc.proc.pid, "port": svc.port,
                "exitCode": code, "startedAt": int(svc.started_at), "logPath": str(svc.log_path)}

    def owner_of_token(self, token: str) -> str | None:
        with self._lock:
            for known, ext_id in self._tokens.items():
                if secrets.compare_digest(known, token or ""):
                    return ext_id if self._alive(ext_id) else None
        return None

    # ---- 代轉前端請求（bridge service.request）----
    def request(self, ext_id: str, path: str, method: str = "GET", headers: dict | None = None, body: str | None = None) -> dict:
        svc = self._procs.get(ext_id)
        if not svc or svc.proc.poll() is not None:
            raise ServiceError("SERVICE_NOT_RUNNING", "後端沒有在執行：請先在擴充中心啟動它")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ServiceError("INVALID_ARGUMENT", "path 必須是以 / 開頭的相對路徑")
        verb = (method or "GET").upper()
        if verb not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
            raise ServiceError("INVALID_ARGUMENT", f"不支援的 method：{verb}")
        payload = (body or "").encode("utf-8") if body is not None else None
        if payload and len(payload) > MAX_BODY:
            raise ServiceError("INVALID_ARGUMENT", "body 太大")
        clean = {str(k): str(v) for k, v in (headers or {}).items() if str(k).lower() not in _HOP_HEADERS}
        clean["X-Randy-Hub-Ext"] = ext_id
        conn = http.client.HTTPConnection(_connect_host(svc.host), svc.port, timeout=60)
        try:
            conn.request(verb, path, body=payload, headers=clean)
            resp = conn.getresponse()
            data = resp.read(MAX_BODY + 1)[:MAX_BODY]
            return {"status": resp.status, "headers": dict(resp.getheaders()), "body": data.decode("utf-8", errors="replace")}
        except OSError as exc:
            raise ServiceError("SERVICE_UNREACHABLE", f"連不到後端：{exc}") from exc
        finally:
            conn.close()
