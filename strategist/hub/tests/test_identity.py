"""擴充身分 = manifest id + 來源資料夾：冒用同 id（同版號）的其他資料夾不得繼承授權 / 信任 / 儲存 / 機密。

回歸自獨立安全審查的「id 搶佔」發現（secreview/probe.py 第 5 項）。
"""
from __future__ import annotations

import pytest

from strategist.hub.capabilities import dispatch
from strategist.hub.context import build_context
from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

EXT = "com.test.victim"
PERMS = ["shared-storage", "strategist:command"]


@pytest.fixture()
def ctx(tmp_path):
    context = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    yield context
    context.shutdown()


def _mount(ctx, folder):
    root = write_ext(folder, web_manifest(id=EXT, permissions=PERMS), {"view/index.html": HTML})
    ctx.registry.add_mount(str(root))
    return root


def _arm_original(ctx, tmp_path):
    original = _mount(ctx, tmp_path / "original")
    ctx.registry.set_grant(EXT, "strategist:command", True)
    ctx.registry.set_trust(EXT, True)
    dispatch(ctx, EXT, "storage", "set", {"key": "notes", "value": "私人筆記"})
    ctx.ext_secrets(EXT).set("API_KEY", "original-secret-value")
    return original


def test_squatter_with_same_id_and_version_inherits_nothing(ctx, tmp_path):
    original = _arm_original(ctx, tmp_path)
    original_key = ctx.registry.get(EXT).key
    original_port = ctx.registry.get(EXT).server.port
    ctx.registry.remove_mount(str(original.resolve()))
    _mount(ctx, tmp_path / "evil_squatter")

    assert ctx.registry.get(EXT).key != original_key
    assert ctx.registry.get(EXT).server.port != original_port  # 不同 origin → 碰不到原擴充的 localStorage
    assert ctx.registry.is_granted(EXT, "strategist:command") is False
    assert ctx.registry.is_trusted(EXT) is False
    assert dispatch(ctx, EXT, "storage", "get", {"key": "notes"}) is None
    assert ctx.ext_secrets(EXT).get("API_KEY") is None
    assert any("來源" in w for w in ctx.registry.identity_warnings(EXT))
    assert any(a["action"] == "identity-changed" for a in ctx.db.recent_audit())


def test_original_folder_gets_everything_back(ctx, tmp_path):
    original = _arm_original(ctx, tmp_path)
    ctx.registry.remove_mount(str(original.resolve()))
    squatter = _mount(ctx, tmp_path / "evil_squatter")
    ctx.registry.remove_mount(str(squatter.resolve()))
    ctx.registry.add_mount(str(original))

    assert ctx.registry.is_granted(EXT, "strategist:command") is True
    assert ctx.registry.is_trusted(EXT) is True
    assert dispatch(ctx, EXT, "storage", "get", {"key": "notes"}) == "私人筆記"
    assert ctx.ext_secrets(EXT).get("API_KEY") == "original-secret-value"


def test_install_key_is_stable_and_path_bound(ctx, tmp_path):
    _mount(ctx, tmp_path / "original")
    key = ctx.registry.get(EXT).key
    ctx.registry.refresh()
    assert ctx.registry.get(EXT).key == key
    assert key.startswith(f"{EXT}@") and len(key.split("@")[1]) == 12
