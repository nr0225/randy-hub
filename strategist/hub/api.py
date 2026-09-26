"""Hub JSON API 路由（Randy 自有模組；x-hub 的對應物是 191 個 Tauri 命令）。

所有 /api/* 由 server.py 先做 Host / Token / Origin 檢查後才進到這裡。
"""
from __future__ import annotations

import platform
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .backup import create_backup
from .capabilities import BridgeError, capability_table, dispatch
from .context import STRATEGIST_GATEWAY_KEY, STRATEGIST_ROOT_KEY, strategist_from_settings
from .extensions import LOAD_EXAMPLES_KEY
from .keychain import SecretError
from .manifest import ManifestError
from .providers import PRESETS, ProviderError
from .services import ServiceError
from .strategist_adapter import StrategistAdapter, StrategistError

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.extra = status, code, extra or {}


@dataclass
class Request:
    method: str
    path: str
    query: dict
    body: Any
    params: dict = field(default_factory=dict)


ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def route(method: str, pattern: str):
    def register(fn):
        ROUTES.append((method, re.compile(f"^{pattern}$"), fn))
        return fn
    return register


def resolve(method: str, path: str):
    for verb, pattern, fn in ROUTES:
        match = pattern.match(path)
        if match and verb == method:
            return fn, match.groupdict()
    return None, {}


def _body(req: Request) -> dict:
    if not isinstance(req.body, dict):
        raise ApiError(400, "INVALID_ARGUMENT", "需要 JSON 物件 body")
    return req.body


def _ext(hub, ext_id: str):
    loaded = hub.ctx.registry.get(ext_id)
    if not loaded:
        raise ApiError(404, "NOT_FOUND", f"擴充不存在：{ext_id}")
    return loaded


def translate(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, ManifestError):
        return ApiError(400, "MANIFEST_INVALID", "manifest 驗證失敗", {"errors": exc.errors})
    if isinstance(exc, ProviderError):
        status = 404 if exc.code == "NOT_FOUND" else 502 if exc.code in {"UNREACHABLE", "HTTP_ERROR", "BAD_RESPONSE"} else 400
        return ApiError(status, exc.code, str(exc))
    if isinstance(exc, ServiceError):
        return ApiError(409, exc.code, str(exc))
    if isinstance(exc, (StrategistError, SecretError, ValueError)):
        return ApiError(400, "INVALID_ARGUMENT", str(exc))
    if isinstance(exc, KeyError):
        return ApiError(404, "NOT_FOUND", f"找不到：{exc}")
    return ApiError(500, "INTERNAL", f"Hub 內部錯誤：{type(exc).__name__}")


# ---- 狀態 / 設定 ----
SETTING_KEYS = ("theme.mode", "theme.accent", "dashboard.layout", STRATEGIST_ROOT_KEY, STRATEGIST_GATEWAY_KEY,
                LOAD_EXAMPLES_KEY)


def _validate_setting(key: str, value: Any) -> Any:
    if key == "theme.mode" and value not in ("light", "dark", "system"):
        raise ApiError(400, "INVALID_ARGUMENT", "theme.mode 只允許 light/dark/system")
    if key == "theme.accent" and not (isinstance(value, str) and _HEX_COLOR.match(value)):
        raise ApiError(400, "INVALID_ARGUMENT", "theme.accent 必須是 #rrggbb")
    if key == "dashboard.layout":
        ok = isinstance(value, list) and len(value) <= 64 and all(
            isinstance(v, dict) and isinstance(v.get("id"), str) and len(v["id"]) <= 200 for v in value)
        if not ok:
            raise ApiError(400, "INVALID_ARGUMENT", "dashboard.layout 格式錯誤")
        return [{"id": v["id"], "hidden": bool(v.get("hidden"))} for v in value]
    if key == STRATEGIST_ROOT_KEY and not (isinstance(value, str) and Path(value).expanduser().joinpath("strategist").is_dir()):
        raise ApiError(400, "INVALID_ARGUMENT", "Strategist 根目錄底下必須有 strategist/ 資料夾")
    if key == STRATEGIST_GATEWAY_KEY:
        StrategistAdapter._check_gateway(str(value))
    if key == LOAD_EXAMPLES_KEY and not isinstance(value, bool):
        raise ApiError(400, "INVALID_ARGUMENT", f"{LOAD_EXAMPLES_KEY} 必須是 true / false")
    return value


@route("GET", "/api/state")
def get_state(hub, _req):
    ctx = hub.ctx
    settings = {k: ctx.db.get_setting(k) for k in SETTING_KEYS}
    settings[STRATEGIST_ROOT_KEY] = str(ctx.strategist.root)
    settings[STRATEGIST_GATEWAY_KEY] = ctx.strategist.gateway_url
    settings[LOAD_EXAMPLES_KEY] = ctx.registry.examples_enabled()
    return {"version": __version__, "origin": hub.origin, "dataRoot": str(ctx.paths.root),
            "startedAt": int(ctx.started_at), "settings": settings, "nativeShell": hub.native_shell}


@route("PUT", "/api/settings")
def put_settings(hub, req):
    updates = _body(req)
    unknown = [k for k in updates if k not in SETTING_KEYS]
    if unknown:
        raise ApiError(400, "INVALID_ARGUMENT", f"不允許的設定：{unknown}")
    for key, value in updates.items():
        hub.ctx.db.set_setting(key, _validate_setting(key, value))
    if STRATEGIST_ROOT_KEY in updates or STRATEGIST_GATEWAY_KEY in updates:
        hub.ctx.strategist = strategist_from_settings(hub.ctx.db)
    if LOAD_EXAMPLES_KEY in updates:
        hub.ctx.registry.refresh()
    return get_state(hub, req)


@route("GET", "/api/audit")
def get_audit(hub, req):
    return {"items": hub.ctx.db.recent_audit(min(int(req.query.get("limit", 50)), 500))}


@route("POST", "/api/backup")
def post_backup(hub, _req):
    return {"path": str(create_backup(hub.ctx))}


@route("GET", "/api/doctor")
def get_doctor(hub, _req):
    ctx = hub.ctx
    node = shutil.which("node")
    checks = [
        ("macOS / Apple Silicon", platform.system() == "Darwin", f"{platform.system()} {platform.mac_ver()[0]} {platform.machine()}"),
        ("Python", sys.version_info >= (3, 10), sys.version.split()[0]),
        ("資料根可寫", ctx.paths.root.is_dir(), str(ctx.paths.root)),
        ("Keychain 後端", True, type(ctx.secrets._backend).__name__),
        ("Node（service 擴充用，選配）", bool(node), node or "未安裝：node 類 service 擴充無法啟動"),
        ("Strategist 根目錄", (ctx.strategist.root / "strategist").is_dir(), str(ctx.strategist.root)),
        ("Strategist 閘道", ctx.strategist.health().get("ok", False), ctx.strategist.gateway_url),
        ("擴充載入錯誤", not ctx.registry.errors, f"{len(ctx.registry.errors)} 個目錄載入失敗"),
    ]
    return {"checks": [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in checks]}


# ---- Strategist（唯讀狀態 + 使用者明確送出的指令）----
@route("GET", "/api/strategist/status")
def get_strategist(hub, _req):
    return hub.ctx.strategist.status()


@route("POST", "/api/strategist/command")
def post_strategist_command(hub, req):
    body = _body(req)
    hub.ctx.db.audit("user", "strategist-command", {"target": body.get("target", ""), "chars": len(str(body.get("command", "")))})
    return hub.ctx.strategist.command(str(body.get("command", "")), str(body.get("target", "")), str(body.get("context", "")))


# ---- AI 供應商 ----
@route("GET", "/api/providers")
def get_providers(hub, _req):
    presets = [{"id": k, **{f: v for f, v in p.items() if f in ("name", "baseUrl", "needsKey")}} for k, p in PRESETS.items()]
    return {"items": hub.ctx.providers.list(), "presets": presets}


@route("POST", "/api/providers")
def post_provider(hub, req):
    body = _body(req)
    return hub.ctx.providers.create(str(body.get("preset", "")), body.get("name"), body.get("baseUrl"))


@route("PUT", r"/api/providers/(?P<pid>[a-z0-9._-]+)")
def put_provider(hub, req):
    return hub.ctx.providers.update(req.params["pid"], _body(req))


@route("DELETE", r"/api/providers/(?P<pid>[a-z0-9._-]+)")
def delete_provider(hub, req):
    hub.ctx.providers.delete(req.params["pid"])
    return {"ok": True}


@route("PUT", r"/api/providers/(?P<pid>[a-z0-9._-]+)/key")
def put_provider_key(hub, req):
    return hub.ctx.providers.set_key(req.params["pid"], str(_body(req).get("key", "")))


@route("DELETE", r"/api/providers/(?P<pid>[a-z0-9._-]+)/key")
def delete_provider_key(hub, req):
    return hub.ctx.providers.clear_key(req.params["pid"])


@route("POST", r"/api/providers/(?P<pid>[a-z0-9._-]+)/test")
def post_provider_test(hub, req):
    return hub.ctx.providers.test(req.params["pid"])


@route("GET", r"/api/providers/(?P<pid>[a-z0-9._-]+)/models")
def get_provider_models(hub, req):
    return {"models": hub.ctx.providers.fetch_models(req.params["pid"])}


@route("POST", r"/api/providers/(?P<pid>[a-z0-9._-]+)/chat")
def post_provider_chat(hub, req):
    body = _body(req)
    return hub.ctx.providers.chat(req.params["pid"], str(body.get("prompt", "")), body.get("model"))


# ---- 擴充 ----
def _ext_view(hub, loaded) -> dict:
    ctx, m = hub.ctx, loaded.manifest
    view = m.summary()
    view.update({
        "source": loaded.source, "path": loaded.path, "origin": loaded.server.origin,
        "grants": ctx.registry.grant_table(m.id), "trusted": ctx.registry.is_trusted(m.id),
        "trustState": ctx.registry.trust_state(m.id),
        "pinned": ctx.registry.is_pinned(m.id), "missingCapabilities": ctx.registry.missing_capabilities(m),
        "missingDependencies": ctx.registry.missing_dependencies(m), "secrets": ctx.ext_secrets(m.id).names(),
        "service": ctx.services.status(m.id) if m.runtime == "service" else None,
        "backend": {"engine": m.backend.engine_type, "host": m.backend.host} if m.backend else None,
        "installKey": loaded.key, "warnings": list(m.warnings) + ctx.registry.identity_warnings(m.id),
    })
    return view


@route("GET", "/api/extensions")
def get_extensions(hub, _req):
    reg = hub.ctx.registry
    return {"items": [_ext_view(hub, e) for e in reg.all()], "errors": reg.errors,
            "mounts": hub.ctx.db.list_mounts(), "capabilities": capability_table()}


@route("POST", "/api/extensions/refresh")
def post_refresh(hub, req):
    hub.ctx.registry.refresh()
    return get_extensions(hub, req)


@route("POST", "/api/extensions/mounts")
def post_mount(hub, req):
    manifest = hub.ctx.registry.add_mount(str(_body(req).get("path", "")))
    return {"ok": True, "id": manifest.id}


@route("DELETE", "/api/extensions/mounts")
def delete_mount(hub, req):
    return {"ok": hub.ctx.registry.remove_mount(str(_body(req).get("path", "")))}


@route("PUT", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/grants")
def put_grant(hub, req):
    body = _body(req)
    hub.ctx.registry.set_grant(_ext(hub, req.params["eid"]).id, str(body.get("permission", "")), bool(body.get("granted")))
    if body.get("permission") == "network" and not body.get("granted"):
        hub.ctx.services.stop(req.params["eid"])
    return _ext_view(hub, _ext(hub, req.params["eid"]))


@route("PUT", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/trust")
def put_trust(hub, req):
    loaded = _ext(hub, req.params["eid"])
    trusted = bool(_body(req).get("trusted"))
    hub.ctx.registry.set_trust(loaded.id, trusted)
    if not trusted:
        hub.ctx.services.stop(loaded.id)
    return _ext_view(hub, loaded)


@route("PUT", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/pin")
def put_pin(hub, req):
    loaded = _ext(hub, req.params["eid"])
    hub.ctx.registry.set_pinned(loaded.id, bool(_body(req).get("pinned")))
    return _ext_view(hub, loaded)


@route("POST", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/service/(?P<action>start|stop)")
def post_service(hub, req):
    loaded = _ext(hub, req.params["eid"])
    if req.params["action"] == "start":
        return hub.ctx.services.start(loaded.id)
    hub.ctx.services.stop(loaded.id)
    return hub.ctx.services.status(loaded.id)


@route("GET", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/frame")
def get_frame(hub, req):
    loaded = _ext(hub, req.params["eid"])
    surface = str(req.query.get("surface", "view"))
    url = hub.ctx.registry.frame_url(loaded.id, surface, req.query.get("variant") or None)
    return {"url": url, "origin": loaded.server.origin, "extId": loaded.id, "surface": surface}


@route("GET", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/stamp")
def get_stamp(hub, req):
    loaded = _ext(hub, req.params["eid"])
    return {"stamp": hub.ctx.registry.dev_stamp(loaded.id) if loaded.source != "installed" else ""}


@route("PUT", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/secrets")
def put_ext_secret(hub, req):
    body = _body(req)
    loaded = _ext(hub, req.params["eid"])
    hub.ctx.ext_secrets(loaded.id).set(str(body.get("name", "")), str(body.get("value", "")))
    hub.ctx.db.audit("user", "ext-secret-set", {"ext": loaded.id, "name": body.get("name")})
    return {"names": hub.ctx.ext_secrets(loaded.id).names()}


@route("DELETE", r"/api/extensions/(?P<eid>[a-z0-9._-]+)/secrets")
def delete_ext_secret(hub, req):
    loaded = _ext(hub, req.params["eid"])
    hub.ctx.ext_secrets(loaded.id).delete(str(_body(req).get("name", "")))
    return {"names": hub.ctx.ext_secrets(loaded.id).names()}


# ---- 橋呼叫（宿主 UI 已核對 iframe 的 source 與 origin 後才轉進來）----
@route("POST", r"/api/ext/(?P<eid>[a-z0-9._-]+)/call")
def post_bridge_call(hub, req):
    body = _body(req)
    try:
        data = dispatch(hub.ctx, req.params["eid"], str(body.get("namespace", "")), str(body.get("method", "")), body.get("args", {}))
        return {"ok": True, "data": data}
    except BridgeError as exc:
        return {"ok": False, "error": exc.to_dict()}
