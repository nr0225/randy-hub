"""擴充註冊表：掃描已安裝 / 開發直掛目錄、權限授予、版本信任、每擴充內容伺服器。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — extension.rs（掃描/權限/熱更新戳）、
docs/adr/0007（service 預設不啟動、版本變化重新授權）。
Randy 修改：權限授予有風險分級（高危預設關）、授予紀錄綁版本、資料全放 Hub 資料庫而非擴充目錄點檔；
**擴充身分 = manifest id + 來源資料夾**（安裝金鑰 `<id>@<路徑雜湊>`）：授權、信任、儲存、設定、機密、
專屬埠都綁金鑰——別的資料夾冒用同 id（即使同版號）拿到的是全新空身分（獨立安全審查「id 搶佔」的修正）。
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .content_server import ExtensionContentServer
from .db import HubDB
from .manifest import PERMISSIONS, Manifest, ManifestError, default_granted, load_manifest
from .paths import HubPaths


def install_key(manifest: Manifest) -> str:
    """安裝金鑰：id + 來源資料夾（已 resolve）的摘要。資料夾一換，身分就換。"""
    return f"{manifest.id}@{hashlib.sha256(str(manifest.root).encode('utf-8')).hexdigest()[:12]}"


def tree_files(root: Path):
    """擴充目錄內的檔案（跳過點檔 / 點目錄與 node_modules），依相對路徑排序。"""
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).parts
        if any(p.startswith(".") or p == "node_modules" for p in rel) or not path.is_file():
            continue
        yield "/".join(rel), path


def tree_stamp(root: Path) -> str:
    """便宜的變動戳（路徑 + mtime + 大小）：熱重載與摘要快取用。"""
    digest = hashlib.sha1()
    for rel, path in tree_files(root):
        stat = path.stat()
        digest.update(f"{rel}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    return digest.hexdigest()[:16]


def code_digest(root: Path) -> str:
    """內容摘要（逐檔 sha256）：信任綁它，同版號下改了程式碼也要重新信任。"""
    digest = hashlib.sha256()
    for rel, path in tree_files(root):
        digest.update(rel.encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()[:16]


@dataclass
class LoadedExtension:
    manifest: Manifest
    source: str  # installed / dev / builtin
    path: str
    server: ExtensionContentServer

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def key(self) -> str:
        return install_key(self.manifest)


EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"
LOAD_EXAMPLES_KEY = "hub.loadExamples"


class ExtensionRegistry:
    def __init__(self, paths: HubPaths, db: HubDB, hub_origin: str,
                 capability_names: Callable[[], set[str]], examples: bool | None = None):
        self.paths = paths
        self.db = db
        self.hub_origin = hub_origin
        self._capability_names = capability_names
        self._examples_override = examples
        self._lock = threading.RLock()
        self._loaded: dict[str, LoadedExtension] = {}
        self._identity_notes: dict[str, list[str]] = {}
        self._digest_cache: dict[str, tuple[str, str]] = {}
        self.errors: list[dict] = []

    # ---- 掃描 ----
    def examples_enabled(self) -> bool:
        if self._examples_override is not None:
            return self._examples_override
        return bool(self.db.get_setting(LOAD_EXAMPLES_KEY, True))

    def _candidate_dirs(self) -> list[tuple[str, Path]]:
        found: list[tuple[str, Path]] = []
        if self.paths.extensions.is_dir():
            found += [("installed", p) for p in sorted(self.paths.extensions.iterdir()) if (p / "manifest.json").is_file()]
        found += [("dev", Path(p)) for p in self.db.list_mounts()]
        if self.examples_enabled() and EXAMPLES_DIR.is_dir():  # 內建範例：每次從程式目錄載入，不寫死路徑
            found += [("builtin", p) for p in sorted(EXAMPLES_DIR.iterdir()) if (p / "manifest.json").is_file()]
        return found

    def _apply_policies(self, manifest: Manifest) -> None:
        key = install_key(manifest)
        seen = self.db.ext_state(key).get("seen_version")
        others = [k for k in self.db.keys_for_id(manifest.id) if k != key]
        if seen is None and others:  # 同一個 id 第一次出現在「別的資料夾」
            note = (f"這個 id 先前是由別的資料夾提供；來源已變更為 {manifest.root}。"
                    "授權、信任、儲存與機密都不會沿用，需要重新確認。")
            self._identity_notes[manifest.id] = [note]
            self.db.audit(manifest.id, "identity-changed", {"path": str(manifest.root), "previousKeys": len(others)})
        if seen and seen != manifest.version:
            high = [p for p, info in PERMISSIONS.items() if info.risk == "high"]
            self.db.drop_grants(key, high)
            self.db.set_trusted_version(key, None)
            self.db.audit(manifest.id, "version-changed", {"from": seen, "to": manifest.version})
        if seen != manifest.version:
            self.db.set_seen_version(key, manifest.version)

    def _serve(self, manifest: Manifest) -> ExtensionContentServer:
        ext_id, key = manifest.id, install_key(manifest)
        preferred = self.db.ext_state(key).get("port") or 0
        server = ExtensionContentServer(
            lambda: self._manifest_of(ext_id), self.hub_origin,
            lambda: self.is_granted(ext_id, "network"), preferred,
            avoid_ports=self.db.ports_except(key),  # 別的身分用過的埠 = 別人的 origin（含其 localStorage），不能撿
        ).start()
        if server.port != preferred:
            self.db.set_ext_port(key, server.port)
        return server

    def _manifest_of(self, ext_id: str) -> Manifest | None:
        loaded = self._loaded.get(ext_id)
        return loaded.manifest if loaded else None

    def refresh(self) -> None:
        with self._lock:
            errors: list[dict] = []
            fresh: dict[str, tuple[Manifest, str, str]] = {}
            for source, folder in self._candidate_dirs():
                try:
                    manifest = load_manifest(folder)
                except ManifestError as exc:
                    errors.append({"path": str(folder), "source": source, "errors": exc.errors})
                    continue
                if manifest.id in fresh:
                    same_dir = Path(fresh[manifest.id][2]).resolve() == folder.resolve()
                    if not same_dir and source != "builtin":  # 內建範例被使用者的同 id 擴充覆蓋屬正常，不報錯
                        errors.append({"path": str(folder), "source": source,
                                       "errors": [f"id 重複：{manifest.id}（已由 {fresh[manifest.id][2]} 載入）"]})
                    continue
                self._apply_policies(manifest)
                fresh[manifest.id] = (manifest, source, str(folder))
            for ext_id in list(self._loaded):
                if ext_id not in fresh:
                    self._loaded.pop(ext_id).server.stop()
            for ext_id, (manifest, source, folder) in fresh.items():
                self._load_one(ext_id, manifest, source, folder)
            self.errors = errors

    def _load_one(self, ext_id: str, manifest: Manifest, source: str, folder: str) -> None:
        current = self._loaded.get(ext_id)
        if current and current.key == install_key(manifest):
            current.manifest, current.source, current.path = manifest, source, folder
            return
        if current:
            current.server.stop()  # 身分換了（資料夾不同）：換新 origin
        loaded = LoadedExtension(manifest, source, folder, None)  # type: ignore[arg-type]
        self._loaded[ext_id] = loaded
        loaded.server = self._serve(manifest)

    def stop(self) -> None:
        with self._lock:
            for loaded in self._loaded.values():
                loaded.server.stop()
            self._loaded.clear()

    # ---- 查詢 ----
    def get(self, ext_id: str) -> LoadedExtension | None:
        with self._lock:
            return self._loaded.get(ext_id)

    def all(self) -> list[LoadedExtension]:
        with self._lock:
            return sorted(self._loaded.values(), key=lambda e: e.manifest.name)

    def identity_warnings(self, ext_id: str) -> list[str]:
        return list(self._identity_notes.get(ext_id, []))

    def is_granted(self, ext_id: str, permission: str) -> bool:
        loaded = self.get(ext_id)
        if not loaded or permission not in loaded.manifest.permissions:
            return False
        decision = self.db.grants(loaded.key).get(permission)
        return decision["granted"] if decision else default_granted(permission)

    def grant_table(self, ext_id: str) -> list[dict]:
        loaded = self.get(ext_id)
        if not loaded:
            return []
        rows = []
        for perm in loaded.manifest.permissions:
            info = PERMISSIONS[perm]
            rows.append({"permission": perm, "risk": info.risk, "label": info.label,
                         "implemented": info.implemented, "granted": self.is_granted(ext_id, perm)})
        return rows

    def _trust_token(self, loaded: LoadedExtension) -> str:
        root = loaded.manifest.root
        stamp = tree_stamp(root)
        cached = self._digest_cache.get(loaded.key)
        if not cached or cached[0] != stamp:
            cached = (stamp, code_digest(root))
            self._digest_cache[loaded.key] = cached
        return f"{loaded.manifest.version}#{cached[1]}"

    def trust_state(self, ext_id: str) -> str:
        """trusted / code-changed（同版號但程式碼在信任後被改過）/ untrusted。"""
        loaded = self.get(ext_id)
        if not loaded:
            return "untrusted"
        stored = self.db.ext_state(loaded.key).get("trusted_version") or ""
        if stored and stored == self._trust_token(loaded):
            return "trusted"
        return "code-changed" if stored.startswith(f"{loaded.manifest.version}#") else "untrusted"

    def is_trusted(self, ext_id: str) -> bool:
        return self.trust_state(ext_id) == "trusted"

    def missing_capabilities(self, manifest: Manifest) -> list[str]:
        names = self._capability_names()
        return [r for r in manifest.requires if r not in names]

    def missing_dependencies(self, manifest: Manifest) -> list[str]:
        return [d for d in manifest.depends_on if self.get(d) is None]

    # ---- 變更 ----
    def set_grant(self, ext_id: str, permission: str, granted: bool) -> None:
        loaded = self.get(ext_id)
        if not loaded:
            raise KeyError(ext_id)
        if permission not in loaded.manifest.permissions:
            raise ValueError("只能授予 manifest 已宣告的權限")
        self.db.set_grant(loaded.key, permission, granted, loaded.manifest.version)
        self.db.audit("user", "grant" if granted else "revoke", {"ext": ext_id, "permission": permission})

    def set_trust(self, ext_id: str, trusted: bool) -> None:
        loaded = self.get(ext_id)
        if not loaded:
            raise KeyError(ext_id)
        token = self._trust_token(loaded) if trusted else None  # 版本 + 程式碼摘要
        self.db.set_trusted_version(loaded.key, token)
        self.db.audit("user", "trust" if trusted else "untrust", {"ext": ext_id, "version": loaded.manifest.version,
                                                                  "digest": token.split("#")[1] if token else None})

    def set_pinned(self, ext_id: str, pinned: bool) -> None:
        loaded = self.get(ext_id)
        if not loaded:
            raise KeyError(ext_id)
        self.db.set_pinned(loaded.key, pinned)

    def is_pinned(self, ext_id: str) -> bool:
        loaded = self.get(ext_id)
        return bool(loaded) and bool(self.db.ext_state(loaded.key).get("pinned"))

    def add_mount(self, raw_path: str) -> Manifest:
        folder = Path(str(raw_path or "")).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise ValueError("請給擴充原始碼資料夾的絕對路徑")
        manifest = load_manifest(folder)  # 先驗證，失敗直接丟 ManifestError
        self.db.add_mount(str(folder.resolve()))
        self.db.audit("user", "mount", {"path": str(folder.resolve()), "ext": manifest.id})
        self.refresh()
        return manifest

    def remove_mount(self, raw_path: str) -> bool:
        removed = self.db.remove_mount(str(raw_path))
        if removed:
            self.db.audit("user", "unmount", {"path": str(raw_path)})
            self.refresh()
        return removed

    # ---- 前端需要的資訊 ----
    def frame_url(self, ext_id: str, surface: str, variant: str | None = None) -> str:
        loaded = self.get(ext_id)
        if not loaded:
            raise KeyError(ext_id)
        entry = loaded.manifest.entries.get(surface)
        if not entry:
            raise ValueError(f"{ext_id} 沒有 {surface} 形態")
        url = f"{loaded.server.origin}/{quote(entry)}"
        return f"{url}?xhub-variant={quote(variant)}" if variant else url

    def dev_stamp(self, ext_id: str) -> str:
        """開發熱重載戳：目錄樹（跳過點檔與 node_modules）的相對路徑 + mtime 摘要。"""
        loaded = self.get(ext_id)
        if not loaded or loaded.source == "installed":
            return ""
        return tree_stamp(loaded.manifest.root)
