"""獨立安全審查（2026-09-24）的發現 → 回歸測試。編號對應審查報告：M1/M2/M3、L3-L6、I3、H1 延伸（信任綁程式碼）。"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from strategist.hub import macos
from strategist.hub.backup import restore_backup
from strategist.hub.capabilities import BridgeError, dispatch
from strategist.hub.context import build_context, strategist_from_settings
from strategist.hub.db import HubDB
from strategist.hub.keychain import MemoryBackend, SecretStore, mask
from strategist.hub.paths import hub_paths
from strategist.hub.providers import ProviderError, ProviderManager
from strategist.hub.strategist_adapter import sanitize_settings
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext


def _server(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ---- M2：provider key 不跟著轉址外送 ----
def test_provider_does_not_follow_redirects_with_key(tmp_path):
    stolen = []

    class Attacker(BaseHTTPRequestHandler):
        def do_GET(self):
            stolen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    attacker = _server(Attacker)

    class Redirector(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{attacker.server_address[1]}/v1/models")
            self.end_headers()

        def log_message(self, *a):
            pass

    redirector = _server(Redirector)
    try:
        db = HubDB(tmp_path / "hub.db")
        manager = ProviderManager(db, SecretStore(db, MemoryBackend()))
        created = manager.create("custom", base_url=f"http://127.0.0.1:{redirector.server_address[1]}/v1")
        manager.set_key(created["id"], "sk-must-not-leak-123456")
        with pytest.raises(ProviderError) as info:
            manager.fetch_models(created["id"])
        assert info.value.code == "REDIRECT_BLOCKED"
        assert stolen == []
    finally:
        attacker.shutdown()
        redirector.shutdown()


# ---- M1：來源不明的備份不帶入授權 / 信任 / 掛載 / 壞設定 ----
def _evil_backup(tmp_path):
    db_path = tmp_path / "evil.db"
    db = HubDB(db_path)
    db.set_grant("com.evil.x@0123456789ab", "strategist:command", True, "0.1.0")
    db.set_trusted_version("com.evil.x@0123456789ab", "0.1.0")
    db.add_mount("/Volumes/EVIL/ext")
    db.set_setting("strategist.gatewayUrl", "http://10.6.6.6:5001")
    db.set_setting("theme.mode", "dark")
    db.close()
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("backup.json", json.dumps({"format": 1, "createdAt": "x"}))
        zf.write(db_path, "hub.db")
    return archive


def test_restore_drops_security_state_and_bad_settings(tmp_path):
    paths = hub_paths(tmp_path / "home")
    result = restore_backup(paths, _evil_backup(tmp_path))
    assert result["removedMounts"] == ["/Volumes/EVIL/ext"]
    assert result["clearedGrants"] == 1 and result["clearedTrust"] == 1
    assert "strategist.gatewayUrl" in result["droppedSettings"]
    conn = sqlite3.connect(str(paths.db))
    assert conn.execute("SELECT COUNT(*) FROM ext_grants").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM ext_mounts").fetchone()[0] == 0
    conn.close()
    ctx = build_context(paths, "http://127.0.0.1:9999", MemoryBackend(), examples=False)  # 不可因壞設定起不來
    try:
        assert ctx.db.get_setting("theme.mode") == "dark"  # 合法設定保留
    finally:
        ctx.shutdown()


def test_invalid_stored_gateway_falls_back_instead_of_crashing(tmp_path):
    db = HubDB(tmp_path / "hub.db")
    db.set_setting("strategist.gatewayUrl", "http://10.6.6.6:5001")
    assert strategist_from_settings(db).gateway_url == "http://127.0.0.1:5001"


# ---- M3：存檔不可偽裝副檔名、不跟符號連結、要有 quarantine ----
def test_save_file_is_defanged(tmp_path, monkeypatch):
    monkeypatch.setenv("RANDY_HUB_DOWNLOADS", str(tmp_path / "dl"))
    ctx = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    root = write_ext(tmp_path / "ext", web_manifest(id="com.test.fs", permissions=["fs"]), {"view/index.html": HTML})
    ctx.registry.add_mount(str(root))
    try:
        saved = dispatch(ctx, "com.test.fs", "fs", "saveText", {"name": "發票‮txt.command", "content": "echo pwn"})
        assert "‮" not in saved["name"] and saved["name"].endswith(".txt")
        attrs = subprocess.run(["/usr/bin/xattr", "-p", "com.apple.quarantine", saved["path"]], capture_output=True, text=True)
        assert attrs.returncode == 0 and "Randy Hub" in attrs.stdout
    finally:
        ctx.shutdown()


def test_save_never_follows_planted_symlink(tmp_path):
    folder = tmp_path / "dl"
    folder.mkdir()
    victim = tmp_path / "victim.txt"
    os.symlink(victim, folder / "note.txt")  # 失效連結：exists() 為 False
    path = macos.write_new_file(folder, "note.txt", b"data")
    assert not victim.exists() and path.name != "note.txt" and path.read_bytes() == b"data"


# ---- H1 延伸：信任綁「版本 + 程式碼摘要」----
def test_trust_resets_when_code_changes_under_same_version(tmp_path):
    ctx = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    root = write_ext(tmp_path / "svc", web_manifest(id="com.test.trust"), {"view/index.html": HTML, "view/app.js": "1"})
    ctx.registry.add_mount(str(root))
    try:
        ctx.registry.set_trust("com.test.trust", True)
        assert ctx.registry.trust_state("com.test.trust") == "trusted"
        (root / "view" / "app.js").write_text("2", encoding="utf-8")
        assert ctx.registry.is_trusted("com.test.trust") is False
        assert ctx.registry.trust_state("com.test.trust") == "code-changed"
    finally:
        ctx.shutdown()


# ---- L3：事件名保留、payload 上限 ----
def test_event_rules(tmp_path):
    ctx = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    root = write_ext(tmp_path / "ev", web_manifest(id="com.test.ev", permissions=["events"]), {"view/index.html": HTML})
    ctx.registry.add_mount(str(root))
    try:
        for bad in ({"event": "theme-changed"}, {"event": "ok", "payload": "x" * (64 * 1024 + 1)}):
            with pytest.raises(BridgeError):
                dispatch(ctx, "com.test.ev", "events", "emit", bad)
    finally:
        ctx.shutdown()


# ---- L4：OLLAMA_HOST 沒寫 scheme 也不外洩帳密；擴充看不到本機絕對路徑 ----
def test_ollama_host_without_scheme_is_scrubbed():
    route = sanitize_settings({"OLLAMA_HOST": "user:pw-SECRET@ollama.local:11434"})["route"]
    assert "SECRET" not in json.dumps(route) and "user" not in json.dumps(route)
    assert "SECRET" not in json.dumps(sanitize_settings({"OLLAMA_HOST": "http://u:SECRET@h:notaport"}))


def test_extension_facing_status_has_no_absolute_paths(tmp_path, monkeypatch):
    ctx = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    root = write_ext(tmp_path / "sr", web_manifest(id="com.test.sr", permissions=["strategist:read"]), {"view/index.html": HTML})
    ctx.registry.add_mount(str(root))
    try:
        status = dispatch(ctx, "com.test.sr", "strategist", "status", {})
        assert str(ctx.strategist.root) not in json.dumps(status, ensure_ascii=False)
    finally:
        ctx.shutdown()


# ---- L6：短 key 不露字 ----
def test_mask_short_keys():
    assert mask("sk-123456789") == "••••"
    assert mask("sk-abcdefghijklmnop") == "••••mnop"
