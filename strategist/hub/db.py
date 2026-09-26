"""Randy Hub 本地資料層（SQLite, WAL）。

對應 x-hub `db.rs` + `config.rs` 的角色，但只保留 Hub 需要的表：設定、擴充掛載、
權限授予、信任版本、擴充私有儲存、共享儲存、AI 供應商（非機密欄位）、機密索引、稽核。
機密值本身一律不進這個資料庫（見 keychain.py）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
AUDIT_KEEP = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ext_mounts (path TEXT PRIMARY KEY, added_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ext_state (
    ext_id TEXT PRIMARY KEY, trusted_version TEXT, pinned INTEGER NOT NULL DEFAULT 0,
    port INTEGER, seen_version TEXT, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ext_grants (
    ext_id TEXT NOT NULL, permission TEXT NOT NULL, granted INTEGER NOT NULL,
    version TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (ext_id, permission));
CREATE TABLE IF NOT EXISTS ext_storage (
    ext_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (ext_id, key));
CREATE TABLE IF NOT EXISTS ext_config (
    ext_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (ext_id, key));
CREATE TABLE IF NOT EXISTS shared_storage (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS secret_index (
    owner TEXT NOT NULL, name TEXT NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (owner, name));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, detail TEXT NOT NULL);
"""


def _now() -> int:
    return int(time.time())


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class HubDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    # ---- 基礎 ----
    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup_to(self, target: Path) -> None:
        """用 SQLite backup API 取一致性快照（WAL 模式下直接複製檔案不安全）。"""
        dest = sqlite3.connect(str(target))
        try:
            with self._lock:
                self._conn.backup(dest)
        finally:
            dest.close()

    # ---- 設定 ----
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self._rows("SELECT value FROM settings WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, _dump(value), _now()),
        )

    def all_settings(self) -> dict:
        return {row["key"]: json.loads(row["value"]) for row in self._rows("SELECT key, value FROM settings")}

    # ---- 開發目錄掛載（x-hub「我的擴展」）----
    def list_mounts(self) -> list[str]:
        return [row["path"] for row in self._rows("SELECT path FROM ext_mounts ORDER BY added_at, path")]

    def add_mount(self, path: str) -> None:
        self._exec("INSERT OR IGNORE INTO ext_mounts(path, added_at) VALUES(?, ?)", (path, _now()))

    def remove_mount(self, path: str) -> bool:
        return self._exec("DELETE FROM ext_mounts WHERE path = ?", (path,)).rowcount > 0

    # ---- 擴充狀態（信任版本 / 釘選 / 固定埠 / 已見版本）----
    # 註：ext_state / ext_grants / ext_storage / ext_config 的 ext_id 欄位存的是「安裝金鑰」<id>@<路徑雜湊>
    def keys_for_id(self, ext_id: str) -> list[str]:
        prefix = f"{ext_id}@"
        rows = self._rows("SELECT ext_id FROM ext_state WHERE substr(ext_id, 1, ?) = ?", (len(prefix), prefix))
        return [row["ext_id"] for row in rows]

    def ports_except(self, key: str) -> set[int]:
        rows = self._rows("SELECT port FROM ext_state WHERE port IS NOT NULL AND ext_id != ?", (key,))
        return {int(row["port"]) for row in rows}

    def ext_state(self, ext_id: str) -> dict:
        rows = self._rows("SELECT * FROM ext_state WHERE ext_id = ?", (ext_id,))
        if not rows:
            return {"ext_id": ext_id, "trusted_version": None, "pinned": False, "port": None, "seen_version": None}
        row = dict(rows[0])
        row["pinned"] = bool(row["pinned"])
        return row

    def _upsert_state(self, ext_id: str, column: str, value: Any) -> None:
        if column not in {"trusted_version", "pinned", "port", "seen_version"}:
            raise ValueError(f"unknown ext_state column: {column}")
        self._exec(
            f"INSERT INTO ext_state(ext_id, {column}, updated_at) VALUES(?, ?, ?) "
            f"ON CONFLICT(ext_id) DO UPDATE SET {column} = excluded.{column}, updated_at = excluded.updated_at",
            (ext_id, value, _now()),
        )

    def set_trusted_version(self, ext_id: str, version: str | None) -> None:
        self._upsert_state(ext_id, "trusted_version", version)

    def set_pinned(self, ext_id: str, pinned: bool) -> None:
        self._upsert_state(ext_id, "pinned", 1 if pinned else 0)

    def set_ext_port(self, ext_id: str, port: int | None) -> None:
        self._upsert_state(ext_id, "port", port)

    def set_seen_version(self, ext_id: str, version: str) -> None:
        self._upsert_state(ext_id, "seen_version", version)

    # ---- 權限授予 ----
    def grants(self, ext_id: str) -> dict[str, dict]:
        rows = self._rows("SELECT permission, granted, version FROM ext_grants WHERE ext_id = ?", (ext_id,))
        return {row["permission"]: {"granted": bool(row["granted"]), "version": row["version"]} for row in rows}

    def set_grant(self, ext_id: str, permission: str, granted: bool, version: str) -> None:
        self._exec(
            "INSERT INTO ext_grants(ext_id, permission, granted, version, updated_at) VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(ext_id, permission) DO UPDATE SET granted = excluded.granted, "
            "version = excluded.version, updated_at = excluded.updated_at",
            (ext_id, permission, 1 if granted else 0, version, _now()),
        )

    def drop_grants(self, ext_id: str, permissions: list[str]) -> None:
        for permission in permissions:
            self._exec("DELETE FROM ext_grants WHERE ext_id = ? AND permission = ?", (ext_id, permission))

    # ---- 擴充私有儲存（xhub.storage.*，按擴充隔離）----
    def storage_get(self, ext_id: str, key: str) -> Any:
        rows = self._rows("SELECT value FROM ext_storage WHERE ext_id = ? AND key = ?", (ext_id, key))
        return json.loads(rows[0]["value"]) if rows else None

    def storage_set(self, ext_id: str, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO ext_storage(ext_id, key, value) VALUES(?, ?, ?) "
            "ON CONFLICT(ext_id, key) DO UPDATE SET value = excluded.value",
            (ext_id, key, _dump(value)),
        )

    def storage_remove(self, ext_id: str, key: str) -> None:
        self._exec("DELETE FROM ext_storage WHERE ext_id = ? AND key = ?", (ext_id, key))

    def storage_clear(self, ext_id: str) -> None:
        self._exec("DELETE FROM ext_storage WHERE ext_id = ?", (ext_id,))

    def storage_bytes(self, ext_id: str) -> int:
        rows = self._rows("SELECT COALESCE(SUM(LENGTH(value)), 0) AS n FROM ext_storage WHERE ext_id = ?", (ext_id,))
        return int(rows[0]["n"])

    # ---- 跨擴充共享儲存（需 shared-storage 權限）----
    def shared_get(self, key: str) -> Any:
        rows = self._rows("SELECT value FROM shared_storage WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else None

    def shared_set(self, key: str, value: Any, ext_id: str) -> None:
        self._exec(
            "INSERT INTO shared_storage(key, value, updated_by, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (key, _dump(value), ext_id, _now()),
        )

    def shared_remove(self, key: str) -> None:
        self._exec("DELETE FROM shared_storage WHERE key = ?", (key,))

    # ---- 擴充設定覆蓋層（xhub.config.*；x-hub 寫 .config.json，我們寫 DB 以免污染開發源碼目錄）----
    def config_overrides(self, ext_id: str) -> dict:
        rows = self._rows("SELECT key, value FROM ext_config WHERE ext_id = ?", (ext_id,))
        return {row["key"]: json.loads(row["value"]) for row in rows}

    def config_set(self, ext_id: str, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO ext_config(ext_id, key, value) VALUES(?, ?, ?) "
            "ON CONFLICT(ext_id, key) DO UPDATE SET value = excluded.value",
            (ext_id, key, _dump(value)),
        )

    def config_remove(self, ext_id: str, key: str) -> None:
        self._exec("DELETE FROM ext_config WHERE ext_id = ? AND key = ?", (ext_id, key))

    # ---- AI 供應商（只存非機密欄位）----
    def list_providers(self) -> list[dict]:
        return [json.loads(row["data"]) for row in self._rows("SELECT data FROM providers ORDER BY id")]

    def get_provider(self, provider_id: str) -> dict | None:
        rows = self._rows("SELECT data FROM providers WHERE id = ?", (provider_id,))
        return json.loads(rows[0]["data"]) if rows else None

    def save_provider(self, provider: dict) -> None:
        self._exec(
            "INSERT INTO providers(id, data, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
            (provider["id"], _dump(provider), _now()),
        )

    def delete_provider(self, provider_id: str) -> bool:
        return self._exec("DELETE FROM providers WHERE id = ?", (provider_id,)).rowcount > 0

    # ---- 機密索引（只記「有哪些名字」，值在 Keychain）----
    def secret_names(self, owner: str) -> list[str]:
        rows = self._rows("SELECT name FROM secret_index WHERE owner = ? ORDER BY name", (owner,))
        return [row["name"] for row in rows]

    def add_secret_name(self, owner: str, name: str) -> None:
        self._exec(
            "INSERT INTO secret_index(owner, name, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(owner, name) DO UPDATE SET updated_at = excluded.updated_at",
            (owner, name, _now()),
        )

    def remove_secret_name(self, owner: str, name: str) -> None:
        self._exec("DELETE FROM secret_index WHERE owner = ? AND name = ?", (owner, name))

    # ---- 稽核 ----
    def audit(self, actor: str, action: str, detail: Any = "") -> None:
        text = detail if isinstance(detail, str) else _dump(detail)
        self._exec("INSERT INTO audit(ts, actor, action, detail) VALUES(?, ?, ?, ?)", (_now(), actor, action, text[:2000]))
        self._exec("DELETE FROM audit WHERE id <= (SELECT MAX(id) FROM audit) - ?", (AUDIT_KEEP,))

    def recent_audit(self, limit: int = 50) -> list[dict]:
        rows = self._rows("SELECT ts, actor, action, detail FROM audit ORDER BY id DESC LIMIT ?", (int(limit),))
        return [dict(row) for row in rows]
