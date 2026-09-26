"""擴充內容伺服器：每擴充獨立 origin、點檔/機密檔/逃逸一律拒絕（x-hub ADR 0008 的 macOS 版）。"""
from __future__ import annotations

import http.client
import os

import pytest

from strategist.hub.content_server import ExtensionContentServer, build_csp, inject_bridge
from strategist.hub.manifest import load_manifest
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

HUB_ORIGIN = "http://127.0.0.1:9999"


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("content")
    outside = tmp_path / "outside.txt"
    outside.write_text("TOP-SECRET", encoding="utf-8")
    root = write_ext(
        tmp_path / "ext",
        web_manifest(),
        {
            "view/index.html": HTML,
            "assets/app.js": "console.log('ok')",
            ".storage.json": "{}",
            ".data/state.json": "{}",
            "secrets.json": "{}",
            "app.db": "sqlite",
            "server.pem": "pem",
            "tool.exe": "MZ",
        },
    )
    os.symlink(outside, root / "leak.txt")
    manifest = load_manifest(root)
    srv = ExtensionContentServer(lambda: manifest, HUB_ORIGIN, lambda: False)
    srv.start()
    yield srv
    srv.stop()


def _get(srv, path, host=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host or f"127.0.0.1:{srv.port}")
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_entry_html_gets_bridge_and_csp(server):
    resp, body = _get(server, "/view/index.html")
    assert resp.status == 200
    html = body.decode()
    assert html.index('<script src="/__randy_hub__/bridge.js"></script>') < html.index("<title>")
    csp = resp.getheader("Content-Security-Policy")
    assert f"frame-ancestors {HUB_ORIGIN}" in csp
    assert "form-action 'none'" in csp and "https:" not in csp
    assert resp.getheader("X-Content-Type-Options") == "nosniff"


def test_static_asset_served_without_injection(server):
    resp, body = _get(server, "/assets/app.js")
    assert resp.status == 200 and b"bridge" not in body


def test_bridge_script_knows_hub_origin(server):
    resp, body = _get(server, "/__randy_hub__/bridge.js")
    assert resp.status == 200
    assert HUB_ORIGIN.encode() in body
    assert b"postMessage" in body


@pytest.mark.parametrize(
    "path",
    ["/.storage.json", "/.data/state.json", "/secrets.json", "/app.db", "/server.pem", "/tool.exe", "/leak.txt",
     "/%2e%2e/outside.txt", "/view/..%2f..%2foutside.txt", "/view/%5c..%5coutside.txt", "/"],
)
def test_sensitive_or_escaping_paths_blocked(server, path):
    resp, body = _get(server, path)
    assert resp.status in (400, 403, 404), path
    assert b"TOP-SECRET" not in body


def test_service_worker_script_fetch_refused(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", "/assets/app.js", headers={"Host": f"127.0.0.1:{server.port}", "Service-Worker": "script"})
    assert conn.getresponse().status == 403
    conn.close()


def test_dns_rebinding_host_rejected(server):
    resp, _ = _get(server, "/view/index.html", host="evil.example:80")
    assert resp.status == 403


def test_only_get_head(server):
    resp, _ = _get(server, "/view/index.html", method="POST")
    assert resp.status == 405


def test_network_permission_opens_https_in_csp():
    assert "https: wss:" in build_csp(True, HUB_ORIGIN)
    assert "https:" not in build_csp(False, HUB_ORIGIN)


def test_inject_bridge_positions():
    tag = "<script>B</script>"
    assert inject_bridge("<html><head><title>x</title></head></html>", tag).startswith("<html><head><script>B</script>")
    assert inject_bridge("<p>no head</p>", tag).startswith(tag)
    assert inject_bridge("<html><HEAD lang='x'><title>x</title></HEAD></html>", tag).find(tag) < 30
