"""備份 / 還原（x-hub「数据备份与恢复」的 macOS 版）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx)：打包成單一 zip（資料庫 + 已安裝擴充）。
Randy 修改：
  - 資料庫用 SQLite backup API 取一致性快照（WAL 模式下不能直接複製檔案）
  - 還原前逐項驗證 zip 成員（擋絕對路徑、..、反斜線、符號連結、非預期檔案、總量上限）
  - 還原把目前資料搬到 backups/pre-restore-<時間>/ 當回滾點，不刪任何東西
  - 備份內容不可信（安全審查 M1）：已授予的權限、信任、掛載作廢，設定逐項驗證，壞的丟掉
  - API key 在 macOS Keychain，不在備份內（換機要重新輸入或靠 iCloud 鑰匙圈）
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from . import __version__
from .paths import HubPaths

PREFIX = "randy-hub-backup-"
MAX_MEMBERS = 20_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class BackupError(ValueError):
    pass


def create_backup(ctx) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = ctx.paths.backups / f"{PREFIX}{stamp}.zip"
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "hub.db"
        ctx.db.backup_to(snapshot)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            meta = {"format": 1, "createdAt": stamp, "hubVersion": __version__,
                    "note": "API key 存在 macOS Keychain，不包含在備份內"}
            zf.writestr("backup.json", json.dumps(meta, ensure_ascii=False, indent=2))
            zf.write(snapshot, "hub.db")
            for path in sorted(ctx.paths.extensions.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, path.relative_to(ctx.paths.root).as_posix())
    target.chmod(0o600)
    ctx.db.audit("user", "backup", {"path": str(target)})
    return target


def validate_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    infos = zf.infolist()
    if len(infos) > MAX_MEMBERS:
        raise BackupError("備份檔成員太多")
    total = 0
    names = set()
    for info in infos:
        name = info.filename
        parts = PurePosixPath(name).parts
        if name.startswith("/") or "\\" in name or ".." in parts or ":" in name:
            raise BackupError(f"不安全的路徑：{name}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise BackupError(f"備份內不允許符號連結：{name}")
        if not (name in ("backup.json", "hub.db") or (parts and parts[0] == "extensions")):
            raise BackupError(f"非預期的檔案：{name}")
        total += info.file_size
        names.add(name)
    if total > MAX_TOTAL_BYTES:
        raise BackupError("備份檔解壓後太大")
    if not {"backup.json", "hub.db"} <= names:
        raise BackupError("不是 Randy Hub 備份檔（缺 backup.json 或 hub.db）")
    return infos


def _check_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.execute("SELECT COUNT(*) FROM settings").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"備份內資料庫無法讀取：{exc}") from exc
    finally:
        conn.close()
    if result != "ok":
        raise BackupError(f"備份內資料庫完整性檢查失敗：{result}")


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _setting_ok(key: str, value) -> bool:
    if key == "theme.mode":
        return value in ("light", "dark", "system")
    if key == "theme.accent":
        return isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is not None
    if key == "hub.loadExamples":
        return isinstance(value, bool)
    if key == "dashboard.layout":
        return isinstance(value, list) and len(value) <= 64 and all(isinstance(v, dict) and isinstance(v.get("id"), str) for v in value)
    if key == "strategist.gatewayUrl":
        parts = urlsplit(str(value))
        return parts.scheme == "http" and (parts.hostname or "") in _LOOPBACK
    if key == "strategist.root":
        return isinstance(value, str) and Path(value).expanduser().joinpath("strategist").is_dir()
    return False


def _sanitize_security_state(db_path: Path) -> dict:
    """備份來源不可信：已授予的權限、信任、掛載一律作廢（使用者的「拒絕」保留），設定逐項驗證。"""
    conn = sqlite3.connect(str(db_path))
    try:
        mounts = [row[0] for row in conn.execute("SELECT path FROM ext_mounts ORDER BY path")]
        grants = conn.execute("DELETE FROM ext_grants WHERE granted = 1").rowcount
        trust = conn.execute("UPDATE ext_state SET trusted_version = NULL WHERE trusted_version IS NOT NULL").rowcount
        conn.execute("DELETE FROM ext_mounts")
        dropped = []
        for key, raw in conn.execute("SELECT key, value FROM settings").fetchall():
            try:
                value = json.loads(raw)
            except ValueError:
                value = None
            if not _setting_ok(key, value):
                dropped.append(key)
        conn.executemany("DELETE FROM settings WHERE key = ?", [(key,) for key in dropped])
        conn.commit()
    finally:
        conn.close()
    return {"removedMounts": mounts, "clearedGrants": grants, "clearedTrust": trust, "droppedSettings": dropped}


def restore_backup(paths: HubPaths, zip_path: Path) -> dict:
    """呼叫前必須確認 Hub 沒有在執行（CLI 會檢查）。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = paths.root / f".restore-{stamp}"
    with zipfile.ZipFile(zip_path) as zf:
        members = validate_members(zf)
        meta = json.loads(zf.read("backup.json").decode("utf-8"))
        if meta.get("format") != 1:
            raise BackupError("不支援的備份格式版本")
        zf.extractall(staging, members)
    _check_db(staging / "hub.db")
    report = _sanitize_security_state(staging / "hub.db")
    rollback = paths.backups / f"pre-restore-{stamp}"
    rollback.mkdir(parents=True)
    for name in ("hub.db", "hub.db-wal", "hub.db-shm"):
        if (paths.root / name).exists():
            shutil.move(str(paths.root / name), str(rollback / name))
    if paths.extensions.exists():
        shutil.move(str(paths.extensions), str(rollback / "extensions"))
    shutil.move(str(staging / "hub.db"), str(paths.db))
    staged_ext = staging / "extensions"
    shutil.move(str(staged_ext), str(paths.extensions)) if staged_ext.exists() else paths.extensions.mkdir()
    shutil.rmtree(staging, ignore_errors=True)
    return {"restored": True, "createdAt": meta.get("createdAt"), "rollback": str(rollback), **report}
