"""Randy Hub 測試共用設定：確保 repo 根在 sys.path，且絕不碰真實 Keychain / 使用者資料根。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ["RANDY_HUB_KEYCHAIN"] = "memory"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RANDY_HUB_HOME", str(tmp_path / "hub-home"))
    monkeypatch.setenv("RANDY_HUB_KEYCHAIN", "memory")
    yield


def write_ext(root: Path, manifest: dict, files: dict[str, str] | None = None) -> Path:
    """在 root 下建立一個擴充目錄（manifest + 指定檔案）。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for rel, text in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return root


def web_manifest(**overrides) -> dict:
    base = {
        "id": "com.randy.test",
        "name": "測試擴充",
        "version": "0.1.0",
        "runtime": "web",
        "kind": "view",
        "surfaces": ["view"],
        "entry": {"view": "./view/index.html"},
        "permissions": [],
    }
    base.update(overrides)
    return base


HTML = "<!doctype html><html><head><title>t</title></head><body><p>hi</p></body></html>"
