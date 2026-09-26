"""Hub JSON API 端點：供應商 / 擴充管理 / 機密 / 備份 / 稽核 / 健康檢查（走真實 HTTP）。"""
from __future__ import annotations

import pytest

from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths
from strategist.hub.server import HubServer
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext
from strategist.hub.tests.test_server import _req as _raw_req

EXT = "com.test.api"


def _req(hub, method, path, body=None, **kw):
    resp, data = _raw_req(hub, method, path, body=body, **kw)
    return resp.status, data


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api")
    server = HubServer(hub_paths(tmp / "home"), port=0, secret_backend=MemoryBackend()).start_background()
    ext = write_ext(tmp / "ext", web_manifest(id=EXT, permissions=["events", "strategist:command"],
                                              surfaces=["view", "drawer"]), {"view/index.html": HTML})
    server.ctx.registry.add_mount(str(ext))
    server.test_ext_dir = ext
    yield server
    server.shutdown()


def test_provider_crud_via_api(hub):
    status, listing = _req(hub, "GET", "/api/providers")
    assert status == 200 and {p["id"] for p in listing["presets"]} >= {"openai", "anthropic", "nim", "deepseek", "ollama", "mlx"}
    status, created = _req(hub, "POST", "/api/providers", body={"preset": "ollama"})
    assert status == 200 and created["id"] == "ollama"
    status, updated = _req(hub, "PUT", "/api/providers/ollama", body={"name": "我的 Ollama", "models": ["a", "b"], "defaultModel": "a"})
    assert updated["name"] == "我的 Ollama" and updated["models"] == ["a", "b"]
    status, keyed = _req(hub, "PUT", "/api/providers/ollama/key", body={"key": "sk-api-test-123456"})
    assert keyed["hasKey"] is True and "sk-api-test-123456" not in str(keyed)
    assert _req(hub, "DELETE", "/api/providers/ollama/key")[1]["hasKey"] is False
    assert _req(hub, "POST", "/api/providers", body={"preset": "nope"})[0] == 400
    assert _req(hub, "PUT", "/api/providers/ollama", body={"baseUrl": "http://evil.example/v1"})[0] == 400
    assert _req(hub, "DELETE", "/api/providers/ollama")[0] == 200
    assert _req(hub, "PUT", "/api/providers/ollama", body={"name": "x"})[0] == 404


def test_provider_test_reports_unreachable(hub):
    _req(hub, "POST", "/api/providers", body={"preset": "custom", "baseUrl": "http://127.0.0.1:9/v1"})
    status, result = _req(hub, "POST", "/api/providers/custom/test")
    assert status == 200 and result["ok"] is False and result["code"] == "UNREACHABLE"
    assert _req(hub, "GET", "/api/providers/custom/models")[0] == 502
    assert _req(hub, "POST", "/api/providers/custom/chat", body={"prompt": "hi"})[0] == 400  # 沒選預設模型


def test_extension_management_endpoints(hub):
    status, listing = _req(hub, "GET", "/api/extensions")
    item = next(e for e in listing["items"] if e["id"] == EXT)
    assert status == 200 and item["source"] == "dev" and {g["permission"] for g in item["grants"]} == {"events", "strategist:command"}
    view = _req(hub, "PUT", f"/api/extensions/{EXT}/grants", body={"permission": "strategist:command", "granted": True})[1]
    assert next(g for g in view["grants"] if g["permission"] == "strategist:command")["granted"] is True
    assert _req(hub, "PUT", f"/api/extensions/{EXT}/grants", body={"permission": "fs", "granted": True})[0] == 400
    assert _req(hub, "PUT", f"/api/extensions/{EXT}/pin", body={"pinned": True})[1]["pinned"] is True
    assert _req(hub, "PUT", f"/api/extensions/{EXT}/trust", body={"trusted": True})[1]["trusted"] is True
    assert _req(hub, "GET", f"/api/extensions/{EXT}/frame?surface=drawer")[1]["url"].endswith("/view/index.html")
    assert _req(hub, "GET", f"/api/extensions/{EXT}/frame?surface=module")[0] == 400
    assert _req(hub, "GET", f"/api/extensions/{EXT}/stamp")[1]["stamp"]
    assert _req(hub, "POST", f"/api/extensions/{EXT}/service/start")[0] == 409  # 不是 service 擴充
    assert _req(hub, "GET", "/api/extensions/com.nope.x/frame")[0] == 404
    assert _req(hub, "POST", "/api/extensions/refresh")[0] == 200


def test_extension_secrets_names_only(hub):
    status, body = _req(hub, "PUT", f"/api/extensions/{EXT}/secrets", body={"name": "DEMO", "value": "hidden-value-123"})
    assert status == 200 and body["names"] == ["DEMO"] and "hidden-value-123" not in str(body)
    assert _req(hub, "PUT", f"/api/extensions/{EXT}/secrets", body={"name": "../x", "value": "v"})[0] == 400
    assert _req(hub, "DELETE", f"/api/extensions/{EXT}/secrets", body={"name": "DEMO"})[1]["names"] == []


def test_mount_validation(hub, tmp_path):
    assert _req(hub, "POST", "/api/extensions/mounts", body={"path": "relative/path"})[0] == 400
    bad = write_ext(tmp_path / "bad", web_manifest(id="com.test.bad", version="1"), {"view/index.html": HTML})
    status, body = _req(hub, "POST", "/api/extensions/mounts", body={"path": str(bad)})
    assert status == 400 and body["code"] == "MANIFEST_INVALID" and body["errors"]
    assert _req(hub, "DELETE", "/api/extensions/mounts", body={"path": "/not/mounted"})[1]["ok"] is False


def test_backup_audit_doctor_and_strategist(hub):
    assert _req(hub, "POST", "/api/backup")[1]["path"].endswith(".zip")
    audit = _req(hub, "GET", "/api/audit?limit=5")[1]["items"]
    assert audit and all(set(a) == {"ts", "actor", "action", "detail"} for a in audit)
    names = {c["name"] for c in _req(hub, "GET", "/api/doctor")[1]["checks"]}
    assert {"Python", "Keychain 後端", "Strategist 閘道"} <= names
    status = _req(hub, "GET", "/api/strategist/status")
    assert status[0] == 200 and "gateway" in status[1]
    assert _req(hub, "POST", "/api/strategist/command", body={"command": "x", "target": "rm"})[0] == 400


def test_settings_toggle_examples_and_unknown_route(hub):
    assert _req(hub, "PUT", "/api/settings", body={"hub.loadExamples": "yes"})[0] == 400
    assert _req(hub, "PUT", "/api/settings", body={"hub.loadExamples": False})[1]["settings"]["hub.loadExamples"] is False
    assert _req(hub, "PUT", "/api/settings", body={"dashboard.layout": "nope"})[0] == 400
    assert _req(hub, "GET", "/api/does-not-exist")[0] == 404
    assert _req(hub, "PATCH", "/api/state")[0] in (404, 501)
