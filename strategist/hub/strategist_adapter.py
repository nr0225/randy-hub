"""軍師中控（Strategist）唯讀轉接層（Randy 自有模組）。

原則：Hub 不 import、不修改 Strategist。
  - 健康狀態 / 下指令：走現行本機閘道 strategist/web_gateway.py（預設 http://127.0.0.1:5001）
  - provider 插槽表：用 ast 解析 strategist/provider/router.py 的 _SLOTS（不執行任何 Strategist 程式碼）
  - 目前路由：只從 strategist/settings.json 取白名單欄位；任何 *KEY* 只回報「有沒有設定」，絕不回傳值
  - launchd：唯讀 launchctl print，不載入 / 卸載
Provider UI（Hub 自己的 AI 供應商）與這裡的 Strategist routing 刻意解耦：這裡只看、不改。
"""
from __future__ import annotations

import ast
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import macos

DEFAULT_GATEWAY = "http://127.0.0.1:5001"
DEFAULT_LAUNCHD_LABEL = "com.randy.strategist-web"
ALLOWED_TARGETS = ("", "claude", "nim", "gemini", "gpt", "mlx", "ollama", "agent", "coder", "flash", "pro", "journal")  # 與 web_gateway._ALLOWED_TARGETS 一致
ROUTE_KEYS = (
    "STRATEGIST_PROVIDER", "STRATEGIST_MODEL", "STRATEGIST_GEMINI_MODEL", "STRATEGIST_CLAUDE_MODEL",
    "STRATEGIST_OLLAMA_MODEL", "STRATEGIST_GPT_MODEL", "STRATEGIST_GEMINI_KEY_SOURCE", "STRATEGIST_MAX_TOKENS",
)
_SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class StrategistError(RuntimeError):
    pass


def _strip_userinfo(url: str) -> str:
    """去掉帳密；沒寫 scheme（user:pass@host:port）也要能正確辨認主機，解析失敗就整個不回。"""
    text = url if "://" in url else f"//{url}"
    try:
        parts = urlsplit(text)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return ""
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def parse_provider_slots(router_py: Path) -> list[dict]:
    """解析 _SLOTS 字面值；只接受 list[dict[str, str]]，其他一律當作讀不到。"""
    tree = ast.parse(router_py.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_SLOTS" for t in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, list) and all(isinstance(v, dict) for v in value):
                return [{str(k): str(v) for k, v in slot.items()} for slot in value]
    return []


def sanitize_settings(settings: dict) -> dict:
    route = {key: str(settings[key]) for key in ROUTE_KEYS if str(settings.get(key, "")).strip()}
    keys_present = {
        name: bool(str(value).strip())
        for name, value in settings.items()
        if isinstance(name, str) and name not in ROUTE_KEYS and _SECRET_NAME.search(name)
        and not isinstance(value, (dict, list))
    }
    marker = settings.get("__keychain__")  # Strategist 已把這些 key 搬進 Keychain：檔案只剩名稱清單
    if isinstance(marker, dict) and isinstance(marker.get("keys"), list):
        keys_present.update({name: True for name in marker["keys"] if isinstance(name, str) and _SECRET_NAME.search(name)})
    host = str(settings.get("OLLAMA_HOST", "")).strip()
    if host:
        route["OLLAMA_HOST"] = _strip_userinfo(host)
    active_profile = str(settings.get("__active_profile__", "")).strip()
    return {"route": route, "keysPresent": keys_present, "activeProfile": active_profile or None}


class StrategistAdapter:
    def __init__(self, root: Path, gateway_url: str = DEFAULT_GATEWAY, launchd_label: str = DEFAULT_LAUNCHD_LABEL):
        self.root = Path(root)
        self.gateway_url = self._check_gateway(gateway_url)
        self.launchd_label = launchd_label

    @staticmethod
    def _check_gateway(url: str) -> str:
        parts = urlsplit(url or "")
        if parts.scheme != "http" or (parts.hostname or "") not in _LOOPBACK:
            raise StrategistError("Strategist 閘道只允許本機回環位址（http://127.0.0.1:<port>）")
        return url.rstrip("/")

    # ---- 閘道 ----
    def _http(self, method: str, path: str, payload: dict | None = None, timeout: float = 3.0) -> tuple[int, dict]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.gateway_url + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read(1024 * 1024).decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read(64 * 1024).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return exc.code, {}

    def health(self) -> dict:
        started = time.perf_counter()
        try:
            status, body = self._http("GET", "/api/health")
        except (OSError, ValueError) as exc:
            return {"ok": False, "url": self.gateway_url, "error": f"{type(exc).__name__}: 連不到閘道"}
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": status == 200 and bool(body.get("ok")), "url": self.gateway_url, "latencyMs": latency,
                "service": body.get("service", "")}

    def routes(self) -> dict:
        """經閘道讀路由表；只放行白名單欄位（名稱與有沒有設定 key），閘道多給什麼都不轉出去。"""
        try:
            status, body = self._http("GET", "/api/strategist/routes")
        except (OSError, ValueError) as exc:
            return {"ok": False, "routes": [], "error": f"{type(exc).__name__}: 連不到閘道"}
        if status != 200 or not body.get("success"):
            return {"ok": False, "routes": [], "error": str(body.get("message") or f"HTTP {status}")}
        rows = [{"route": str(r.get("route", "")), "provider": str(r.get("provider", "")), "keySlot": str(r.get("keySlot", "")),
                 "keySet": bool(r.get("keySet")), "model": str(r.get("model", ""))}
                for r in body.get("routes", []) if isinstance(r, dict)]
        return {"ok": True, "routes": rows}

    def command(self, text: str, target: str = "", context: str = "") -> dict:
        text = (text or "").strip()
        if not text:
            raise StrategistError("指令不可為空")
        if len(text) > 20_000:
            raise StrategistError("指令太長（上限 20000 字）")
        target = (target or "").strip().lower()
        if target not in ALLOWED_TARGETS:
            raise StrategistError(f"不允許的 target：{target}")
        status, body = self._http("POST", "/api/strategist/command",
                                  {"command": text, "target": target, "context": context or ""}, timeout=600)
        return {"httpStatus": status, **{k: body.get(k) for k in ("success", "reply", "message", "task_type", "target", "next_action")}}

    # ---- 唯讀檔案 ----
    def provider_slots(self) -> dict:
        router = self.root / "strategist" / "provider" / "router.py"
        try:
            return {"ok": True, "slots": parse_provider_slots(router), "source": str(router)}
        except (OSError, SyntaxError, ValueError) as exc:
            return {"ok": False, "slots": [], "source": str(router), "error": type(exc).__name__}

    def active_route(self) -> dict:
        path = self.root / "strategist" / "settings.json"
        if not path.is_file():
            return {"ok": False, "source": str(path), "error": "settings.json 不存在（Strategist 用預設值）"}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return {"ok": False, "source": str(path), "error": type(exc).__name__}
        if not isinstance(data, dict):
            return {"ok": False, "source": str(path), "error": "格式不符"}
        return {"ok": True, "source": str(path), **sanitize_settings(data)}

    def public_status(self) -> dict:
        """給擴充的版本：拿掉本機絕對路徑（根目錄、設定檔與 router.py 位置）。"""
        status = self.status()
        status.pop("root", None)
        status["providers"] = {k: v for k, v in status["providers"].items() if k != "source"}
        status["route"] = {k: v for k, v in status["route"].items() if k != "source"}
        return status

    def status(self) -> dict:
        return {
            "root": str(self.root),
            "rootExists": (self.root / "strategist").is_dir(),
            "gateway": self.health(),
            "launchd": macos.launchctl_status(self.launchd_label),
            "providers": self.provider_slots(),
            "route": self.active_route(),
            "routes": self.routes(),
            "entrypoints": {
                "cao_entry": (self.root / "strategist" / "cao_entry.py").is_file(),
                "web_gateway": (self.root / "strategist" / "web_gateway.py").is_file(),
            },
        }
