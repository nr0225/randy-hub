"""擴充預檢：靜態掃描 xhub.* 呼叫，對帳 manifest 權限（規則沿用 x-hub debug-deploy.md「平台關卡」）。"""
from __future__ import annotations

from strategist.hub.precheck import precheck
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

JS_USES = """
await window.xhub.storage.get('k');
await xhub.sharedStorage.set('a', 1);
await window.xhub.strategist.command('go');
xhub.events.emit('tick', 1);
xhub.events.on('tick', () => {});
xhub.data.notes.list();
"""


def test_missing_and_unused_permissions(tmp_path):
    root = write_ext(tmp_path / "ext", web_manifest(permissions=["events", "notify"]),
                     {"view/index.html": HTML, "view/app.js": JS_USES})
    report = precheck(root)
    errors = " ".join(report["errors"])
    warnings = " ".join(report["warnings"])
    assert "shared-storage" in errors and "strategist:command" in errors
    assert "notify" in warnings  # 宣告了卻沒用到
    assert "data.notes.list" in warnings  # Randy Hub 沒提供
    assert report["ok"] is False


def test_clean_extension_passes(tmp_path):
    root = write_ext(tmp_path / "ext", web_manifest(permissions=["events"]),
                     {"view/index.html": HTML, "view/app.js": "xhub.events.emit('x'); xhub.storage.set('a', 1);"})
    report = precheck(root)
    assert report["ok"] is True and report["errors"] == []


def test_dotfiles_and_node_modules_skipped(tmp_path):
    root = write_ext(tmp_path / "ext", web_manifest(),
                     {"view/index.html": HTML, ".data/x.js": "xhub.fs.saveText({})", "node_modules/y/z.js": "xhub.fs.saveText({})"})
    assert precheck(root)["ok"] is True


def test_invalid_manifest_reported(tmp_path):
    root = write_ext(tmp_path / "ext", web_manifest(version="1.0"), {"view/index.html": HTML})
    report = precheck(root)
    assert report["ok"] is False and any("version" in e for e in report["errors"])
