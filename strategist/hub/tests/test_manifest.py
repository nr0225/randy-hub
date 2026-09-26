"""manifest 驗證：規則沿用 x-hub skills/x-hub-extension/references/manifest.md。"""
from __future__ import annotations

import os

import pytest

from strategist.hub.manifest import ManifestError, load_manifest, PERMISSIONS, default_granted
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext


def _load(tmp_path, manifest, files=None):
    root = write_ext(tmp_path / "ext", manifest, files if files is not None else {"view/index.html": HTML})
    return load_manifest(root)


def _errors(tmp_path, manifest, files=None) -> str:
    with pytest.raises(ManifestError) as info:
        _load(tmp_path, manifest, files)
    return " | ".join(info.value.errors)


def test_valid_web_manifest(tmp_path):
    m = _load(tmp_path, web_manifest())
    assert m.id == "com.randy.test"
    assert m.runtime == "web"
    assert m.surfaces == ("view",)
    assert m.entries["view"] == "view/index.html"
    assert m.disabled_reason is None


@pytest.mark.parametrize("bad_id", ["Com.randy.x", "randy", ".com.randy", "com..randy", "com.randy/x", "com.randy x", "a." + "b" * 130])
def test_bad_ids_rejected(tmp_path, bad_id):
    assert "id" in _errors(tmp_path, web_manifest(id=bad_id))


@pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "1.0.0-beta", "", 1])
def test_bad_versions_rejected(tmp_path, bad_version):
    assert "version" in _errors(tmp_path, web_manifest(version=bad_version))


def test_unknown_permission_rejected(tmp_path):
    assert "permission" in _errors(tmp_path, web_manifest(permissions=["root:everything"]))


@pytest.mark.parametrize("entry", ["../outside.html", "/etc/passwd", "view/index.js", "view\\index.html", "./.hidden/index.html"])
def test_bad_entry_paths_rejected(tmp_path, entry):
    assert "entry" in _errors(tmp_path, web_manifest(entry={"view": entry}), {"view/index.html": HTML, ".hidden/index.html": HTML})


def test_missing_entry_file_rejected(tmp_path):
    assert "entry" in _errors(tmp_path, web_manifest(), files={})


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text(HTML, encoding="utf-8")
    root = write_ext(tmp_path / "ext", web_manifest(), {})
    (root / "view").mkdir()
    os.symlink(outside, root / "view" / "index.html")
    with pytest.raises(ManifestError) as info:
        load_manifest(root)
    assert "entry" in " ".join(info.value.errors)


def test_declared_surface_needs_entry_but_window_falls_back_to_view(tmp_path):
    m = _load(tmp_path, web_manifest(surfaces=["view", "window", "drawer"]))
    assert m.entries["window"] == "view/index.html"
    assert m.entries["drawer"] == "view/index.html"
    assert "module" in _errors(tmp_path, web_manifest(surfaces=["view", "module"]))


def test_service_requires_backend(tmp_path):
    assert "backend" in _errors(tmp_path, web_manifest(runtime="service"))


def _service(**backend):
    spec = {"entry": "./service/main.py", "engine": {"type": "python"}, "port": 0, "health": "/healthz"}
    spec.update(backend)
    return web_manifest(runtime="service", backend=spec)


SERVICE_FILES = {"view/index.html": HTML, "service/main.py": "print('hi')\n"}


def test_service_manifest_ok(tmp_path):
    m = _load(tmp_path, _service(), SERVICE_FILES)
    assert m.backend is not None
    assert m.backend.engine_type == "python"
    assert m.backend.host == "127.0.0.1"
    assert m.backend.health == "/healthz"


def test_public_listen_requires_network_permission(tmp_path):
    assert "network" in _errors(tmp_path, _service(host="0.0.0.0"), SERVICE_FILES)
    manifest = _service(host="0.0.0.0")
    manifest["permissions"] = ["network"]
    assert _load(tmp_path, manifest, SERVICE_FILES).backend.host == "0.0.0.0"


def test_unknown_engine_rejected(tmp_path):
    assert "engine" in _errors(tmp_path, _service(engine={"type": "ruby"}), SERVICE_FILES)


def test_platform_disabled_on_macos(tmp_path):
    m = _load(tmp_path, web_manifest(disabled={"platform": "macos"}))
    assert m.disabled_reason


def test_reserved_namespace_only_warns(tmp_path):
    m = _load(tmp_path, web_manifest(id="com.x-hub.clone"))
    assert any("保留" in w for w in m.warnings)


def test_invalid_json(tmp_path):
    root = tmp_path / "ext"
    root.mkdir()
    (root / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(root)


def test_high_risk_permissions_default_off():
    assert default_granted("events") is True
    assert default_granted("strategist:read") is True
    for perm, info in PERMISSIONS.items():
        if info.risk == "high":
            assert default_granted(perm) is False, perm
    assert PERMISSIONS["strategist:command"].risk == "high"
    assert PERMISSIONS["network"].risk == "high"
