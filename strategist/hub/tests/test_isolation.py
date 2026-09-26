"""擴充權限隔離：宣告 × 授予兩層檢查、儲存/機密按擴充隔離、高危預設關、版本變更重新授權。"""
from __future__ import annotations

import json

import pytest

from strategist.hub.capabilities import BridgeError, dispatch
from strategist.hub.context import build_context
from strategist.hub.keychain import MemoryBackend, SecretError
from strategist.hub.paths import hub_paths
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

ALPHA, BETA = "com.test.alpha", "com.test.beta"


@pytest.fixture()
def ctx(tmp_path):
    context = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    alpha = write_ext(tmp_path / "alpha", web_manifest(id=ALPHA, permissions=["events"], config={"greeting": "hi"}),
                      {"view/index.html": HTML})
    beta = write_ext(tmp_path / "beta", web_manifest(id=BETA, permissions=["shared-storage", "strategist:read", "strategist:command"],
                                                     expose=["ping"]), {"view/index.html": HTML})
    context.registry.add_mount(str(alpha))
    context.registry.add_mount(str(beta))
    yield context
    context.shutdown()


def _code(ctx, ext_id, ns, method, args=None) -> str:
    with pytest.raises(BridgeError) as info:
        dispatch(ctx, ext_id, ns, method, args or {})
    return info.value.code


def test_storage_is_isolated_per_extension(ctx):
    dispatch(ctx, ALPHA, "storage", "set", {"key": "k", "value": {"n": 1}})
    assert dispatch(ctx, ALPHA, "storage", "get", {"key": "k"}) == {"n": 1}
    assert dispatch(ctx, BETA, "storage", "get", {"key": "k"}) is None
    dispatch(ctx, BETA, "storage", "clear", {})
    assert dispatch(ctx, ALPHA, "storage", "get", {"key": "k"}) == {"n": 1}


def test_undeclared_permission_denied_and_audited(ctx):
    assert _code(ctx, ALPHA, "sharedStorage", "get", {"key": "x"}) == "PERMISSION_DENIED"
    assert any(a["action"] == "permission-denied" and a["actor"] == ALPHA for a in ctx.db.recent_audit())


def test_high_risk_declared_but_off_until_granted(ctx, monkeypatch):
    sent = []
    monkeypatch.setattr(ctx.strategist, "command", lambda text, target="", context="": sent.append(text) or {"success": True})
    assert _code(ctx, BETA, "strategist", "command", {"command": "hi"}) == "PERMISSION_DENIED"
    assert sent == []
    ctx.registry.set_grant(BETA, "strategist:command", True)
    assert dispatch(ctx, BETA, "strategist", "command", {"command": "hi"})["success"] is True
    ctx.registry.set_grant(BETA, "strategist:command", False)
    assert _code(ctx, BETA, "strategist", "command", {"command": "again"}) == "PERMISSION_DENIED"
    assert sent == ["hi"]


def test_cannot_grant_what_manifest_did_not_declare(ctx):
    with pytest.raises(ValueError):
        ctx.registry.set_grant(ALPHA, "strategist:command", True)


def test_medium_permission_default_on_and_revocable(ctx):
    dispatch(ctx, BETA, "sharedStorage", "set", {"key": "s", "value": 1})
    assert dispatch(ctx, BETA, "sharedStorage", "get", {"key": "s"}) == 1
    ctx.registry.set_grant(BETA, "shared-storage", False)
    assert _code(ctx, BETA, "sharedStorage", "get", {"key": "s"}) == "PERMISSION_DENIED"


def test_xhub_data_namespace_reports_unavailable(ctx):
    assert _code(ctx, ALPHA, "data", "notes.list") == "CAPABILITY_UNAVAILABLE"


def test_runtime_info_lists_real_capabilities(ctx):
    info = dispatch(ctx, ALPHA, "runtime", "info", {})
    names = {f"{c['namespace']}.{c['method']}" for c in info["capabilities"]}
    assert {"storage.get", "strategist.command", "service.request"} <= names
    assert info["platform"] == "macos" and info["permissions"] == {"events": True}


def test_call_extension_only_for_exposed_methods(ctx):
    assert dispatch(ctx, ALPHA, "runtime", "callExtension", {"targetId": BETA, "method": "ping"}) == {"ok": True}
    assert _code(ctx, ALPHA, "runtime", "callExtension", {"targetId": BETA, "method": "steal"}) == "PERMISSION_DENIED"


def test_events_emit_needs_permission_and_rejects_reserved_prefix(ctx):
    assert dispatch(ctx, ALPHA, "events", "emit", {"event": "tick"}) == {"ok": True}
    assert _code(ctx, ALPHA, "events", "emit", {"event": "xhub:variant-changed"}) == "INVALID_ARGUMENT"
    assert _code(ctx, BETA, "events", "emit", {"event": "tick"}) == "PERMISSION_DENIED"


def test_version_change_resets_high_risk_grant_and_trust(ctx, tmp_path):
    ctx.registry.set_grant(BETA, "strategist:command", True)
    ctx.registry.set_trust(BETA, True)
    assert ctx.registry.is_trusted(BETA)
    manifest_path = tmp_path / "beta" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["version"] = "0.2.0"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    ctx.registry.refresh()
    assert not ctx.registry.is_granted(BETA, "strategist:command")
    assert not ctx.registry.is_trusted(BETA)
    assert ctx.registry.is_granted(BETA, "shared-storage")  # 中風險維持


def test_scoped_secrets_cannot_cross_extensions(ctx):
    ctx.ext_secrets(ALPHA).set("TOKEN", "alpha-only-secret")
    assert ctx.ext_secrets(BETA).get("TOKEN") is None
    assert ctx.ext_secrets(ALPHA).get("TOKEN") == "alpha-only-secret"
    assert ctx.ext_secrets(ALPHA).owner.startswith(f"ext:{ALPHA}@")
    with pytest.raises(SecretError):
        ctx.secrets.scoped("../com.test.alpha")
    assert "alpha-only-secret" not in json.dumps(ctx.db.recent_audit())


def test_storage_quota_enforced(ctx):
    assert _code(ctx, ALPHA, "storage", "set", {"key": "big", "value": "x" * (1024 * 1024 + 10)}) == "QUOTA_EXCEEDED"


def test_config_defaults_then_override(ctx):
    assert dispatch(ctx, ALPHA, "config", "get", {"key": "greeting"}) == "hi"
    dispatch(ctx, ALPHA, "config", "set", {"key": "greeting", "value": "yo"})
    assert dispatch(ctx, ALPHA, "config", "all", {}) == {"greeting": "yo"}
    dispatch(ctx, ALPHA, "config", "remove", {"key": "greeting"})
    assert dispatch(ctx, ALPHA, "config", "get", {"key": "greeting"}) == "hi"


def test_open_external_only_http_and_rate_limited(ctx, monkeypatch):
    opened = []
    monkeypatch.setattr("strategist.hub.macos.subprocess.run", lambda args, **kw: opened.append(args))
    assert _code(ctx, ALPHA, "runtime", "openExternal", {"url": "file:///etc/passwd"}) == "INVALID_ARGUMENT"
    ctx.rate_limits.clear()
    assert dispatch(ctx, ALPHA, "runtime", "openExternal", {"url": "https://example.com"}) == {"ok": True}
    assert _code(ctx, ALPHA, "runtime", "openExternal", {"url": "https://example.com"}) == "RATE_LIMITED"
    assert opened == [["/usr/bin/open", "https://example.com"]]


def test_fs_save_text_requires_permission(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("RANDY_HUB_DOWNLOADS", str(tmp_path / "dl"))
    assert _code(ctx, ALPHA, "fs", "saveText", {"name": "a.txt", "content": "x"}) == "PERMISSION_DENIED"
