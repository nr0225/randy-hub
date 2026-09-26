"""擴充 manifest 解析與驗證。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx):
  - 欄位與規則：skills/x-hub-extension/references/manifest.md
  - id / version 格式、保留命名空間 com.x-hub.*、network 只管對外監聽：同 x-hub
Randy 修改：
  - 權限表加上風險等級，高風險預設關閉；新增 strategist:read / strategist:command / ai（保留）
  - backend.engine 支援 python（Randy 生態主力語言），node 保留相容
  - 入口路徑額外做 symlink 逃逸與點檔檢查（x-hub 在內容協議層做，我們在載入時就擋）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

SURFACES = ("module", "view", "window", "drawer")
RUNTIMES = ("web", "service")
ENGINES = ("node", "python")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MAX_MANIFEST_BYTES = 256 * 1024
_ID_RE = re.compile(r"^[a-z0-9._-]+$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_REQUIRE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*\.[A-Za-z0-9_.]+$")
RESERVED_PREFIXES = ("com.x-hub.", "com.randy.hub.")


@dataclass(frozen=True)
class PermissionInfo:
    risk: str  # low / medium / high
    label: str
    implemented: bool = True


PERMISSIONS: dict[str, PermissionInfo] = {
    "events": PermissionInfo("low", "廣播自訂事件給其他擴充"),
    "notify": PermissionInfo("low", "發送 macOS 系統通知"),
    "shared-storage": PermissionInfo("medium", "讀寫跨擴充共享儲存"),
    "fs": PermissionInfo("medium", "把檔案存到「下載」資料夾"),
    "strategist:read": PermissionInfo("medium", "讀取軍師中控狀態（閘道、provider 插槽、目前路由；不含任何 key）"),
    "data:read": PermissionInfo("medium", "讀取宿主資料（x-hub 相容保留；Randy Hub 尚無此資料模型）", False),
    "data:write": PermissionInfo("high", "修改宿主資料（x-hub 相容保留）", False),
    "strategist:command": PermissionInfo("high", "代你向軍師中控下指令（會實際執行任務、可能消耗額度）"),
    "network": PermissionInfo("high", "service 後端對外監聽（非 127.0.0.1）"),
    "clipboard": PermissionInfo("high", "剪貼簿（x-hub planned，尚未實作）", False),
    "system": PermissionInfo("high", "開啟本機 App / 路徑（x-hub planned，尚未實作）", False),
    "ai": PermissionInfo("high", "透過 Hub 的 AI 供應商發請求（保留，尚未開放）", False),
}


def default_granted(permission: str) -> bool:
    """高危能力預設關閉；低、中風險在 manifest 明確宣告時預設開啟（使用者可隨時關）。"""
    info = PERMISSIONS.get(permission)
    return bool(info) and info.risk != "high"


class ManifestError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class BackendSpec:
    entry: str
    engine_type: str
    min_version: str
    cwd: str
    port: int
    host: str
    health: str | None


@dataclass(frozen=True)
class Manifest:
    root: Path
    id: str
    name: str
    version: str
    runtime: str
    kind: str
    surfaces: tuple[str, ...]
    entries: dict[str, str]
    permissions: tuple[str, ...]
    icon: str | None = None
    description: str = ""
    open_in: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    expose: tuple[str, ...] = ()
    actions: tuple[dict, ...] = ()
    config: dict = field(default_factory=dict)
    backend: BackendSpec | None = None
    module_variants: tuple[dict, ...] = ()
    module_options: dict = field(default_factory=dict)
    min_size: dict | None = None
    disabled_reason: str | None = None
    warnings: tuple[str, ...] = ()

    def summary(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version, "runtime": self.runtime,
            "kind": self.kind, "surfaces": list(self.surfaces), "permissions": list(self.permissions),
            "description": self.description, "openIn": list(self.open_in), "expose": list(self.expose),
            "requires": list(self.requires), "dependsOn": list(self.depends_on), "actions": list(self.actions),
            "moduleVariants": list(self.module_variants), "moduleOptions": dict(self.module_options),
            "minSize": self.min_size, "hasIcon": bool(self.icon), "disabledReason": self.disabled_reason,
            "warnings": list(self.warnings),
        }


def safe_relative(root: Path, rel: Any, *, must_exist: bool = True) -> str:
    """把 manifest 裡的相對路徑轉成規範化 posix 字串；逃逸、絕對路徑、點檔、反斜線一律拒絕。"""
    if not isinstance(rel, str) or not rel.strip():
        raise ValueError("路徑必須是非空字串")
    text = rel.strip()
    if "\\" in text or "\0" in text or text.startswith("/") or ":" in text:
        raise ValueError(f"路徑不合法：{text}")
    parts = [p for p in PurePosixPath(text).parts if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith(".") for p in parts):
        raise ValueError(f"路徑不可逃逸或指向點檔：{text}")
    normalized = "/".join(parts)
    base = root.resolve()
    target = (base / normalized).resolve()
    if target != base and base not in target.parents:
        raise ValueError(f"路徑逃出擴充目錄：{text}")
    if must_exist and not target.is_file():
        raise ValueError(f"檔案不存在：{text}")
    return normalized


def _check_id(raw: Any, errors: list[str], warnings: list[str]) -> str:
    ext_id = raw if isinstance(raw, str) else ""
    ok = (
        0 < len(ext_id) <= 128 and "." in ext_id and _ID_RE.match(ext_id)
        and not ext_id.startswith(".") and not ext_id.endswith(".") and ".." not in ext_id
    )
    if not ok:
        errors.append("id 必須是小寫反向域名（含 .，只允許 a-z0-9._-，≤128，不以 . 開頭/結尾，不含 ..）")
    elif ext_id.startswith(RESERVED_PREFIXES):
        warnings.append(f"id 使用了保留命名空間（{', '.join(RESERVED_PREFIXES)}），第三方請改用自己的網域")
    return ext_id


def _check_version(raw: Any, errors: list[str]) -> str:
    if not isinstance(raw, str) or not _VERSION_RE.match(raw):
        errors.append("version 必須是 x.y.z 三段純數字")
        return ""
    return raw


def _string_list(data: dict, key: str, errors: list[str]) -> tuple[str, ...]:
    raw = data.get(key, [])
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(v, str) and v.strip() for v in raw):
        errors.append(f"{key} 必須是字串陣列")
        return ()
    return tuple(v.strip() for v in raw)


def _check_permissions(data: dict, errors: list[str]) -> tuple[str, ...]:
    perms = _string_list(data, "permissions", errors)
    unknown = [p for p in perms if p not in PERMISSIONS]
    if unknown:
        errors.append(f"未知的 permission：{', '.join(unknown)}")
    return tuple(dict.fromkeys(perms))


def _check_surfaces(data: dict, kind: str, errors: list[str]) -> tuple[str, ...]:
    surfaces = _string_list(data, "surfaces", errors) or (kind,)
    bad = [s for s in surfaces if s not in SURFACES]
    if bad:
        errors.append(f"surfaces 只允許 {SURFACES}：{bad}")
    return tuple(dict.fromkeys(s for s in surfaces if s in SURFACES))


def _check_entries(root: Path, data: dict, surfaces: tuple[str, ...], errors: list[str]) -> dict[str, str]:
    raw = data.get("entry", {})
    if not isinstance(raw, dict):
        errors.append("entry 必須是 { surface: html 路徑 }")
        return {}
    entries: dict[str, str] = {}
    for surface, rel in raw.items():
        if surface not in SURFACES:
            errors.append(f"entry 含未知 surface：{surface}")
            continue
        try:
            normalized = safe_relative(root, rel)
        except ValueError as exc:
            errors.append(f"entry.{surface}：{exc}")
            continue
        if not normalized.lower().endswith((".html", ".htm")):
            errors.append(f"entry.{surface} 必須是 HTML 檔")
            continue
        entries[surface] = normalized
    for surface in surfaces:
        if surface in entries:
            continue
        if surface in ("window", "drawer") and "view" in entries:
            entries[surface] = entries["view"]  # x-hub：window/drawer 與 view 共用入口
        else:
            errors.append(f"宣告了 {surface} 形態卻沒有對應 entry（module 形態必須有自己的 entry）")
    return entries


def _check_backend(root: Path, data: dict, permissions: tuple[str, ...], errors: list[str]) -> BackendSpec | None:
    raw = data.get("backend")
    if not isinstance(raw, dict):
        errors.append("runtime=service 必須提供 backend 物件")
        return None
    engine = raw.get("engine", {"type": "node"})
    engine_type = engine.get("type") if isinstance(engine, dict) else None
    if engine_type not in ENGINES:
        errors.append(f"backend.engine.type 只支援 {ENGINES}")
    min_version = str(engine.get("minVersion", "")) if isinstance(engine, dict) else ""
    try:
        entry = safe_relative(root, raw.get("entry"))
    except ValueError as exc:
        errors.append(f"backend.entry：{exc}")
        entry = ""
    cwd = "."
    if raw.get("cwd") not in (None, "", ".", "./"):
        try:
            cwd = safe_relative(root, raw.get("cwd"), must_exist=False)
            if not (root / cwd).is_dir():
                errors.append("backend.cwd 必須是擴充目錄內既有的資料夾")
        except ValueError as exc:
            errors.append(f"backend.cwd：{exc}")
    port = raw.get("port", 0)
    if not isinstance(port, int) or not 0 <= port <= 65535:
        errors.append("backend.port 必須是 0-65535 的整數（建議 0 = 動態分配）")
        port = 0
    host = str(raw.get("host") or "127.0.0.1").strip()
    if host not in LOOPBACK_HOSTS and "network" not in permissions:
        errors.append("backend.host 對外監聽必須宣告 network 權限")
    health = raw.get("health")
    if health is not None and (not isinstance(health, str) or not health.startswith("/")):
        errors.append("backend.health 必須是以 / 開頭的路徑")
        health = None
    return BackendSpec(entry, engine_type or "", min_version, cwd, port, host, health)


def _optional_dict(data: dict, key: str, errors: list[str]) -> dict:
    raw = data.get(key, {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        errors.append(f"{key} 必須是物件")
        return {}
    return raw


def _check_icon(root: Path, data: dict, warnings: list[str]) -> str | None:
    if not data.get("icon"):
        return None
    try:
        icon = safe_relative(root, data["icon"])
    except ValueError as exc:
        warnings.append(f"icon 無法使用：{exc}")
        return None
    if not icon.lower().endswith((".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico")):
        warnings.append("icon 必須是 svg/png/jpg/webp/ico")
        return None
    return icon


def _disabled_reason(data: dict) -> str | None:
    raw = data.get("disabled")
    if isinstance(raw, dict) and str(raw.get("platform", "")).lower() in ("macos", "darwin", "mac"):
        return "manifest.disabled.platform 指定在 macOS 停用"
    return None


def _read_json(root: Path) -> dict:
    path = root / "manifest.json"
    if not path.is_file():
        raise ManifestError([f"找不到 manifest.json：{root}"])
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ManifestError(["manifest.json 太大"])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError([f"manifest.json 不是合法 JSON：{exc}"]) from exc
    if not isinstance(data, dict):
        raise ManifestError(["manifest.json 頂層必須是物件"])
    return data


def load_manifest(root: Path) -> Manifest:
    root = Path(root)
    data = _read_json(root)
    errors: list[str] = []
    warnings: list[str] = []
    ext_id = _check_id(data.get("id"), errors, warnings)
    name = data.get("name") if isinstance(data.get("name"), str) and data["name"].strip() else ""
    if not name:
        errors.append("name 必填")
    version = _check_version(data.get("version"), errors)
    runtime = data.get("runtime", "web")
    if runtime not in RUNTIMES:
        errors.append(f"runtime 只允許 {RUNTIMES}")
    kind = data.get("kind", "view")
    if kind not in SURFACES:
        errors.append(f"kind 只允許 {SURFACES}")
        kind = "view"
    permissions = _check_permissions(data, errors)
    surfaces = _check_surfaces(data, kind, errors)
    entries = _check_entries(root, data, surfaces, errors)
    backend = _check_backend(root, data, permissions, errors) if runtime == "service" else None
    requires = _string_list(data, "requires", errors)
    if any(not _REQUIRE_RE.match(r) for r in requires):
        errors.append("requires 要寫能力名 namespace.method（例如 storage.get），不是權限名")
    min_size = data.get("minSize") if isinstance(data.get("minSize"), dict) else None
    variants = data.get("moduleVariants", [])
    if not isinstance(variants, list) or not all(isinstance(v, dict) and v.get("id") for v in variants):
        errors.append("moduleVariants 必須是含 id 的物件陣列")
        variants = []
    actions = data.get("actions", [])
    if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
        errors.append("actions 必須是物件陣列")
        actions = []
    open_in = _string_list(data, "openIn", errors)
    depends_on = _string_list(data, "dependsOn", errors)
    expose = _string_list(data, "expose", errors)
    config = _optional_dict(data, "config", errors)
    module_options = _optional_dict(data, "moduleOptions", errors)
    if errors:
        raise ManifestError(errors)
    return Manifest(
        root=root.resolve(), id=ext_id, name=name.strip(), version=version, runtime=runtime, kind=kind,
        surfaces=surfaces, entries=entries, permissions=permissions, icon=_check_icon(root, data, warnings),
        description=str(data.get("description", ""))[:500], open_in=open_in, requires=requires,
        depends_on=depends_on, expose=expose, actions=tuple(actions), config=config, backend=backend,
        module_variants=tuple(variants), module_options=module_options, min_size=min_size,
        disabled_reason=_disabled_reason(data), warnings=tuple(warnings),
    )
