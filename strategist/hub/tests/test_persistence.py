"""重開 App 不掉設定：設定、掛載、授權、信任、供應商、機密索引都要在新行程（新 context）後仍在。"""
from __future__ import annotations

from strategist.hub.capabilities import dispatch
from strategist.hub.context import build_context
from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

EXT = "com.test.persist"


def test_everything_survives_restart(tmp_path):
    paths = hub_paths(tmp_path / "home")
    keychain = MemoryBackend()  # 模擬 macOS Keychain：跨行程仍在
    ext_dir = write_ext(tmp_path / "ext", web_manifest(id=EXT, permissions=["strategist:command", "events"]),
                        {"view/index.html": HTML})

    first = build_context(paths, "http://127.0.0.1:9999", keychain, examples=False)
    first.db.set_setting("theme.mode", "dark")
    first.db.set_setting("dashboard.layout", [{"id": "clock", "hidden": False}])
    first.registry.add_mount(str(ext_dir))
    first.registry.set_grant(EXT, "strategist:command", True)
    first.registry.set_grant(EXT, "events", False)
    first.registry.set_trust(EXT, True)
    provider = first.providers.create("ollama")
    first.providers.set_key(provider["id"], "local-token-123456")
    dispatch(first, EXT, "storage", "set", {"key": "counter", "value": 7})
    port_before = first.registry.get(EXT).server.port
    first.shutdown()

    second = build_context(paths, "http://127.0.0.1:9999", keychain, examples=False)
    try:
        assert second.db.get_setting("theme.mode") == "dark"
        assert second.db.get_setting("dashboard.layout") == [{"id": "clock", "hidden": False}]
        assert second.registry.get(EXT) is not None
        assert second.registry.is_granted(EXT, "strategist:command") is True
        assert second.registry.is_granted(EXT, "events") is False
        assert second.registry.is_trusted(EXT) is True
        assert [p["id"] for p in second.providers.list()] == [provider["id"]]
        assert second.providers.list()[0]["hasKey"] is True
        assert dispatch(second, EXT, "storage", "get", {"key": "counter"}) == 7
        assert second.registry.get(EXT).server.port == port_before  # 固定埠 → 擴充 origin 穩定
    finally:
        second.shutdown()


def test_builtin_examples_follow_setting_without_absolute_mounts(tmp_path):
    paths = hub_paths(tmp_path / "home")
    ctx = build_context(paths, "http://127.0.0.1:9999", MemoryBackend())
    try:
        hello = ctx.registry.get("com.randy.hello")
        assert hello is not None and hello.source == "builtin"
        assert ctx.db.list_mounts() == []  # 不把 worktree 的絕對路徑寫進資料庫
        ctx.db.set_setting("hub.loadExamples", False)
        ctx.registry.refresh()
        assert ctx.registry.get("com.randy.hello") is None
    finally:
        ctx.shutdown()


def test_user_mount_overrides_builtin_example_without_error(tmp_path):
    from strategist.hub.extensions import EXAMPLES_DIR

    paths = hub_paths(tmp_path / "home")
    ctx = build_context(paths, "http://127.0.0.1:9999", MemoryBackend())
    try:
        ctx.registry.add_mount(str(EXAMPLES_DIR / "hello"))  # 舊版曾把範例寫成絕對路徑掛載
        assert ctx.registry.get("com.randy.hello").source == "dev"
        assert ctx.registry.errors == []
    finally:
        ctx.shutdown()
