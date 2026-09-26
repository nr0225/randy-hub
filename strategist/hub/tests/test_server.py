"""Hub HTTP 邊界：令牌、Origin、Host、Content-Type、靜態路徑、service 回呼 API。"""
from __future__ import annotations

import http.client
import json

import pytest

from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths
from strategist.hub.server import TOKEN_HEADER, HubServer
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext


@pytest.fixture(scope="module")
def hub(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("server")
    server = HubServer(hub_paths(tmp / "home"), port=0, secret_backend=MemoryBackend()).start_background()
    ext = write_ext(tmp / "ext", web_manifest(id="com.test.web", permissions=["events"]), {"view/index.html": HTML})
    server.ctx.registry.add_mount(str(ext))
    yield server
    server.shutdown()


def _req(hub, method, path, body=None, headers=None, token=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
    conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
    all_headers = {"Host": host or f"127.0.0.1:{hub.port}"}
    if token:
        all_headers[TOKEN_HEADER] = hub.token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})
    if data is not None:
        all_headers["Content-Length"] = str(len(data))
    for key, value in all_headers.items():
        conn.putheader(key, value)
    conn.endheaders(data)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp, json.loads(raw)
    except ValueError:
        return resp, raw


def test_ui_served_without_token_and_locked_down(hub):
    for path in ("/", "/index.html", "/static/index.html"):  # 所有 Hub origin 的 HTML 都不准被嵌入
        resp, body = _req(hub, "GET", path, token=False)
        assert resp.status == 200, path
        assert "frame-ancestors 'none'" in resp.getheader("Content-Security-Policy"), path
        assert hub.token.encode() not in body


def test_api_requires_token(hub):
    assert _req(hub, "GET", "/api/state", token=False)[0].status == 401
    resp, body = _req(hub, "GET", "/api/state")
    assert resp.status == 200 and body["origin"] == hub.origin


def test_foreign_origin_rejected_even_with_token(hub):
    resp, _ = _req(hub, "GET", "/api/state", headers={"Origin": "http://127.0.0.1:1"})
    assert resp.status == 403


def test_extension_origin_rejected(hub):
    ext_origin = hub.ctx.registry.get("com.test.web").server.origin
    resp, _ = _req(hub, "POST", "/api/strategist/command", body={"command": "x"}, headers={"Origin": ext_origin})
    assert resp.status == 403


def test_dns_rebinding_host_rejected(hub):
    assert _req(hub, "GET", "/api/state", host="attacker.example")[0].status == 403


def test_json_content_type_required(hub):
    resp, _ = _req(hub, "POST", "/api/providers", headers={"Content-Type": "text/plain", "Content-Length": "2"})
    assert resp.status in (400, 415)


def test_static_traversal_blocked(hub):
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fserver.py"):
        assert _req(hub, "GET", path, token=False)[0].status == 404


def test_bridge_call_roundtrip_and_denial(hub):
    resp, body = _req(hub, "POST", "/api/ext/com.test.web/call",
                      body={"namespace": "storage", "method": "set", "args": {"key": "a", "value": 1}})
    assert resp.status == 200 and body["ok"] is True
    _, body = _req(hub, "POST", "/api/ext/com.test.web/call",
                   body={"namespace": "strategist", "method": "command", "args": {"command": "x"}})
    assert body["ok"] is False and body["error"]["code"] == "PERMISSION_DENIED"


def test_frame_url_points_to_extension_origin(hub):
    _, body = _req(hub, "GET", "/api/extensions/com.test.web/frame?surface=view")
    assert body["url"].startswith(body["origin"]) and body["origin"] != hub.origin


def test_svc_api_needs_service_token_and_no_browser_origin(hub):
    assert _req(hub, "GET", "/svc-api/whoami", token=False)[0].status == 401
    resp, _ = _req(hub, "GET", "/svc-api/whoami", token=False, headers={"Origin": hub.origin, "Authorization": "Bearer x"})
    assert resp.status == 403


def test_settings_validation_and_persistence(hub):
    resp, body = _req(hub, "PUT", "/api/settings", body={"theme.mode": "dark", "theme.accent": "#112233"})
    assert resp.status == 200 and body["settings"]["theme.mode"] == "dark"
    assert _req(hub, "PUT", "/api/settings", body={"theme.mode": "neon"})[0].status == 400
    assert _req(hub, "PUT", "/api/settings", body={"hub.secretBackdoor": True})[0].status == 400
    assert _req(hub, "PUT", "/api/settings", body={"strategist.gatewayUrl": "http://10.0.0.1:5001"})[0].status == 400


def test_icon_revalidated_on_every_request(hub, tmp_path):
    import os

    secret = tmp_path / "id_rsa_like.svg"
    secret.write_text("<svg>TOP-SECRET</svg>", encoding="utf-8")
    ext_dir = tmp_path / "icon-ext"
    write_ext(ext_dir, web_manifest(id="com.test.icon", icon="./icon.svg"),
              {"view/index.html": HTML, "icon.svg": "<svg xmlns='http://www.w3.org/2000/svg'/>"})
    hub.ctx.registry.add_mount(str(ext_dir))
    assert _req(hub, "GET", "/ext-icon/com.test.icon", token=False)[0].status == 200
    (ext_dir / "icon.svg").unlink()
    os.symlink(secret, ext_dir / "icon.svg")  # 載入後才換成指向目錄外的連結
    resp, body = _req(hub, "GET", "/ext-icon/com.test.icon", token=False)
    assert resp.status == 404 and b"TOP-SECRET" not in (body if isinstance(body, bytes) else json.dumps(body).encode())


def test_run_info_and_token_file_private(hub):
    assert oct(hub.paths.token_file.stat().st_mode & 0o777) == "0o600"
    assert json.loads(hub.paths.run_info.read_text())["port"] == hub.port
