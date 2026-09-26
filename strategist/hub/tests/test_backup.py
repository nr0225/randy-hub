"""備份 / 還原：一致性快照、還原後設定回來、zip-slip 與非預期成員一律拒絕、留回滾點。"""
from __future__ import annotations

import zipfile

import pytest

from strategist.hub.backup import BackupError, create_backup, restore_backup
from strategist.hub.context import build_context
from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths


def test_backup_then_restore_roundtrip(tmp_path):
    paths = hub_paths(tmp_path / "home")
    ctx = build_context(paths, "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    ctx.db.set_setting("theme.mode", "dark")
    (paths.extensions / "com.test.inst").mkdir()
    (paths.extensions / "com.test.inst" / "manifest.json").write_text("{}", encoding="utf-8")
    archive = create_backup(ctx)
    ctx.db.set_setting("theme.mode", "light")
    ctx.shutdown()

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert {"backup.json", "hub.db", "extensions/com.test.inst/manifest.json"} <= names
    assert not any(n.startswith(("run/", "logs/")) for n in names)

    result = restore_backup(paths, archive)
    again = build_context(paths, "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    try:
        assert again.db.get_setting("theme.mode") == "dark"
    finally:
        again.shutdown()
    assert (tmp_path / "home" / "backups").joinpath(result["rollback"].split("/")[-1], "hub.db").exists()


def _zip(path, members: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize("evil", ["../escape.txt", "/abs/path", "extensions/..\\..\\x", "run/hub.token", "C:/win.txt"])
def test_restore_rejects_unsafe_members(tmp_path, evil):
    paths = hub_paths(tmp_path / "home")
    archive = _zip(tmp_path / "evil.zip", {"backup.json": b'{"format": 1}', "hub.db": b"x", evil: b"pwn"})
    with pytest.raises(BackupError):
        restore_backup(paths, archive)
    assert not (tmp_path / "escape.txt").exists()


def test_restore_rejects_corrupt_database(tmp_path):
    paths = hub_paths(tmp_path / "home")
    archive = _zip(tmp_path / "bad.zip", {"backup.json": b'{"format": 1}', "hub.db": b"not a sqlite file"})
    with pytest.raises(BackupError):
        restore_backup(paths, archive)
