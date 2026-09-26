"""橋 API 能力表 + 分派（window.xhub.* → 這裡）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src-tauri/src/xhub_api.rs：
CAPABILITIES 靜態註冊表 `{namespace, method, permission}` + 統一分派；runtime.info 回傳真實能力表，
擴充以它做能力探測與優雅降級。
Randy 修改：
  - 權限在伺服器端檢查兩層：manifest 有宣告 AND 使用者有授予（高危預設未授予）；拒絕寫稽核
  - 不提供 x-hub 的 data.*（筆記/待辦模型不在 Randy Hub），呼叫回 CAPABILITY_UNAVAILABLE
  - 新增 strategist.*、notify.show；storage/config 寫 Hub 資料庫（不寫擴充目錄點檔）
"""
from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit

from . import HOST_NAME, __version__, macos
from .extensions import LoadedExtension
from .keychain import SecretError
from .providers import ProviderError
from .services import ServiceError
from .strategist_adapter import StrategistError

MAX_VALUE_BYTES = 1024 * 1024
MAX_EXT_STORAGE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_dict(self) -> dict:
        return {"code": self.code, "message": str(self)}


@dataclass(frozen=True)
class Capability:
    namespace: str
    method: str
    permission: str | None
    handler: Callable[[Any, LoadedExtension, dict], Any]


_REGISTRY: dict[tuple[str, str], Capability] = {}


def capability(namespace: str, method: str, permission: str | None = None):
    def register(fn):
        _REGISTRY[(namespace, method)] = Capability(namespace, method, permission, fn)
        return fn
    return register


def capability_table() -> list[dict]:
    return [{"namespace": c.namespace, "method": c.method, "permission": c.permission}
            for c in sorted(_REGISTRY.values(), key=lambda c: (c.namespace, c.method))]


def capability_names() -> set[str]:
    return {f"{ns}.{method}" for ns, method in _REGISTRY}


def dispatch(ctx, ext_id: str, namespace: str, method: str, args: Any) -> Any:
    loaded = ctx.registry.get(ext_id)
    if not loaded:
        raise BridgeError("NOT_FOUND", f"擴充不存在：{ext_id}")
    if loaded.manifest.disabled_reason:
        raise BridgeError("DISABLED", loaded.manifest.disabled_reason)
    cap = _REGISTRY.get((str(namespace), str(method)))
    if not cap:
        raise BridgeError("CAPABILITY_UNAVAILABLE", f"Randy Hub 沒有提供 {namespace}.{method}")
    if not isinstance(args, dict):
        raise BridgeError("INVALID_ARGUMENT", "args 必須是物件")
    if cap.permission:
        if cap.permission not in loaded.manifest.permissions:
            _deny(ctx, ext_id, cap, "未在 manifest.permissions 宣告")
        if not ctx.registry.is_granted(ext_id, cap.permission):
            _deny(ctx, ext_id, cap, "使用者未授權（或高危權限預設關閉）")
    try:
        return cap.handler(ctx, loaded, args)
    except BridgeError:
        raise
    except (ServiceError, ProviderError) as exc:
        raise BridgeError(exc.code, str(exc)) from exc
    except (StrategistError, SecretError, ValueError) as exc:
        raise BridgeError("INVALID_ARGUMENT", str(exc)) from exc


def _deny(ctx, ext_id: str, cap: Capability, why: str) -> None:
    ctx.db.audit(ext_id, "permission-denied", {"call": f"{cap.namespace}.{cap.method}", "permission": cap.permission, "why": why})
    raise BridgeError("PERMISSION_DENIED", f"{cap.namespace}.{cap.method} 需要 {cap.permission} 權限：{why}")


def _key(args: dict) -> str:
    key = args.get("key")
    if not isinstance(key, str) or not 0 < len(key) <= 200:
        raise BridgeError("INVALID_ARGUMENT", "key 必須是 1-200 字的字串")
    return key


def _encoded(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise BridgeError("INVALID_ARGUMENT", "value 必須可 JSON 序列化") from exc


# ---- runtime ----
@capability("runtime", "info")
def _runtime_info(ctx, ext: LoadedExtension, _args: dict) -> dict:
    m = ext.manifest
    ready = ctx.services.status(m.id)["ready"] if m.runtime == "service" else False
    return {"id": m.id, "name": m.name, "version": m.version, "runtime": m.runtime, "serviceReady": ready,
            "proxyPrefix": None, "host": HOST_NAME, "hostVersion": __version__, "platform": "macos",
            "capabilities": capability_table(),
            "permissions": {row["permission"]: row["granted"] for row in ctx.registry.grant_table(m.id)}}


@capability("runtime", "callExtension")
def _runtime_call_extension(ctx, _ext: LoadedExtension, args: dict) -> dict:
    target = ctx.registry.get(str(args.get("targetId", "")))
    method = str(args.get("method", ""))
    if not target or method not in target.manifest.expose:
        raise BridgeError("PERMISSION_DENIED", f"目標擴充沒有暴露 {method}")
    return {"ok": True}


def _throttle(ctx, ext_id: str, kind: str, what: str) -> None:
    """每個擴充每種動作每秒最多 1 次（防止狂開瀏覽器 / 洗通知）。"""
    now, key = time.time(), f"{kind}:{ext_id}"
    if now - ctx.rate_limits.get(key, 0) < 1.0:
        raise BridgeError("RATE_LIMITED", f"{what}太頻繁（每秒最多 1 次）")
    ctx.rate_limits[key] = now


@capability("runtime", "openExternal")
def _runtime_open_external(ctx, ext: LoadedExtension, args: dict) -> dict:
    _throttle(ctx, ext.id, "open", "開外部連結")
    url = str(args.get("url", ""))
    macos.open_url(url)
    ctx.db.audit(ext.id, "open-external", {"host": urlsplit(url).hostname})  # 只記網域，不記完整網址
    return {"ok": True}


# ---- storage（按擴充隔離，無需權限）----
@capability("storage", "get")
def _storage_get(ctx, ext, args):
    return ctx.db.storage_get(ext.key, _key(args))


@capability("storage", "set")
def _storage_set(ctx, ext, args):
    key, value = _key(args), args.get("value")
    size = _encoded(value)
    if size > MAX_VALUE_BYTES:
        raise BridgeError("QUOTA_EXCEEDED", "單一值上限 1MB")
    if ctx.db.storage_bytes(ext.key) + size > MAX_EXT_STORAGE_BYTES:
        raise BridgeError("QUOTA_EXCEEDED", "此擴充儲存上限 10MB")
    ctx.db.storage_set(ext.key, key, value)
    return None


@capability("storage", "remove")
def _storage_remove(ctx, ext, args):
    ctx.db.storage_remove(ext.key, _key(args))


@capability("storage", "clear")
def _storage_clear(ctx, ext, _args):
    ctx.db.storage_clear(ext.key)


# ---- sharedStorage（跨擴充，需 shared-storage）----
@capability("sharedStorage", "get", "shared-storage")
def _shared_get(ctx, _ext, args):
    return ctx.db.shared_get(_key(args))


@capability("sharedStorage", "set", "shared-storage")
def _shared_set(ctx, ext, args):
    key, value = _key(args), args.get("value")
    if _encoded(value) > MAX_VALUE_BYTES:
        raise BridgeError("QUOTA_EXCEEDED", "單一值上限 1MB")
    ctx.db.shared_set(key, value, ext.id)


@capability("sharedStorage", "remove", "shared-storage")
def _shared_remove(ctx, _ext, args):
    ctx.db.shared_remove(_key(args))


# ---- config（作者預設 + 使用者覆蓋層）----
@capability("config", "all")
def _config_all(ctx, ext, _args):
    return {**ext.manifest.config, **ctx.db.config_overrides(ext.key)}


@capability("config", "get")
def _config_get(ctx, ext, args):
    key = _key(args)
    overrides = ctx.db.config_overrides(ext.key)
    return overrides[key] if key in overrides else ext.manifest.config.get(key)


@capability("config", "set")
def _config_set(ctx, ext, args):
    key, value = _key(args), args.get("value")
    if _encoded(value) > MAX_VALUE_BYTES:
        raise BridgeError("QUOTA_EXCEEDED", "單一值上限 1MB")
    ctx.db.config_set(ext.key, key, value)


@capability("config", "remove")
def _config_remove(ctx, ext, args):
    ctx.db.config_remove(ext.key, _key(args))


# ---- theme / events ----
@capability("theme", "get")
def _theme_get(ctx, _ext, _args):
    """宿主 UI 通常直接回包（它才知道實際套用的 CSS 變數）；這是無 UI 時的後備值。"""
    mode = ctx.db.get_setting("theme.mode", "system")
    return {"mode": "dark" if mode == "dark" else "light", "preset": "default",
            "accent": ctx.db.get_setting("theme.accent", "#5b5bf5"), "tokens": {}, "wallpaper": {"on": False}}


RESERVED_EVENTS = {"theme-changed"}  # 宿主專用，擴充不可冒充
MAX_EVENT_PAYLOAD = 64 * 1024


@capability("events", "emit", "events")
def _events_emit(_ctx, _ext, args):
    event = args.get("event")
    if not isinstance(event, str) or not 0 < len(event) <= 128 or event.startswith("xhub:") or event in RESERVED_EVENTS:
        raise BridgeError("INVALID_ARGUMENT", "event 名稱必須是 1-128 字，且不可用 xhub: 前綴或宿主保留名稱")
    if _encoded(args.get("payload")) > MAX_EVENT_PAYLOAD:
        raise BridgeError("QUOTA_EXCEEDED", "事件 payload 上限 64KB")
    return {"ok": True}


# ---- fs（存到「下載」）----
def _save_bytes(name: Any, data: bytes) -> dict:
    if len(data) > MAX_FILE_BYTES:
        raise BridgeError("QUOTA_EXCEEDED", "單檔上限 64MB")
    target = macos.write_new_file(macos.downloads_dir(), str(name or "download.txt"), data)
    return {"path": str(target), "name": target.name}


@capability("fs", "saveText", "fs")
def _fs_save_text(_ctx, _ext, args):
    content = args.get("content", "")
    if not isinstance(content, str):
        raise BridgeError("INVALID_ARGUMENT", "content 必須是字串")
    return _save_bytes(args.get("name"), content.encode("utf-8"))


@capability("fs", "saveFile", "fs")
def _fs_save_file(_ctx, _ext, args):
    try:
        data = base64.b64decode(str(args.get("base64", "")), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise BridgeError("INVALID_ARGUMENT", "base64 格式錯誤") from exc
    return _save_bytes(args.get("name"), data)


# ---- notify（macOS 通知）----
@capability("notify", "show", "notify")
def _notify_show(ctx, ext, args):
    _throttle(ctx, ext.id, "notify", "通知")
    macos.notify(str(args.get("title") or ext.manifest.name), str(args.get("body") or ""))
    return {"ok": True}


# ---- service（前端 → Hub 代轉 → 擴充後端）----
@capability("service", "request")
def _service_request(ctx, ext, args):
    if ext.manifest.runtime != "service":
        raise BridgeError("NOT_SERVICE", "只有 runtime=service 的擴充能用 service.request")
    return ctx.services.request(ext.id, args.get("path"), args.get("method") or "GET", args.get("headers"), args.get("body"))


# ---- strategist（軍師中控，唯讀 / 高危下指令）----
@capability("strategist", "status", "strategist:read")
def _strategist_status(ctx, _ext, _args):
    return ctx.strategist.public_status()  # 擴充看不到本機絕對路徑


@capability("strategist", "providers", "strategist:read")
def _strategist_providers(ctx, _ext, _args):
    status = ctx.strategist.public_status()
    return {"slots": status["providers"], "route": status["route"]}


@capability("strategist", "command", "strategist:command")
def _strategist_command(ctx, ext, args):
    ctx.db.audit(ext.id, "strategist-command", {"target": args.get("target", ""), "chars": len(str(args.get("command", "")))})
    return ctx.strategist.command(str(args.get("command", "")), str(args.get("target", "")), str(args.get("context", "")))
