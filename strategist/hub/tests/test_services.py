"""service 執行期：環境變數白名單、預設不啟動、信任版本、令牌對照、對外監聽要授權。"""
from __future__ import annotations

import json

import pytest

from strategist.hub import services as services_mod
from strategist.hub.capabilities import BridgeError, dispatch
from strategist.hub.context import build_context
from strategist.hub.keychain import MemoryBackend
from strategist.hub.paths import hub_paths
from strategist.hub.services import ServiceError, build_service_env
from strategist.hub.tests.conftest import HTML, web_manifest, write_ext

SVC = "com.test.svc"
SERVICE_PY = '''
import http.server, json, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/healthz":
            body = {"ok": True}
        elif self.path == "/api/env":
            body = {"keys": sorted(os.environ)}
        else:
            body = {"path": self.path, "ext": os.environ.get("XHUB_EXT_ID"), "hdr": self.headers.get("X-Randy-Hub-Ext")}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def log_message(self, *a):
        pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


def _service_manifest(**backend):
    spec = {"entry": "./service/main.py", "engine": {"type": "python"}, "port": 0, "health": "/healthz"}
    spec.update(backend)
    return web_manifest(id=SVC, runtime="service", backend=spec,
                        permissions=["network"] if backend.get("host") == "0.0.0.0" else [])


@pytest.fixture()
def ctx(tmp_path):
    context = build_context(hub_paths(tmp_path / "home"), "http://127.0.0.1:9999", MemoryBackend(), examples=False)
    yield context
    context.shutdown()


def _mount(ctx, tmp_path, manifest):
    root = write_ext(tmp_path / "svc", manifest, {"view/index.html": HTML, "service/main.py": SERVICE_PY})
    ctx.registry.add_mount(str(root))


def test_env_is_allowlisted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leak")
    monkeypatch.setenv("NODE_OPTIONS", "--require /tmp/evil.js")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-leak")
    monkeypatch.setenv("PYTHONPATH", "/tmp/evil")
    env = build_service_env("com.x.y", 1234, "127.0.0.1", "tok", "http://127.0.0.1:1", "/usr/local/bin")
    assert not {"OPENAI_API_KEY", "NODE_OPTIONS", "AWS_SECRET_ACCESS_KEY", "PYTHONPATH"} & set(env)
    assert env["PORT"] == "1234" and env["XHUB_EXT_ID"] == "com.x.y" and env["RANDY_HUB_SERVICE_TOKEN"] == "tok"
    assert env["PATH"].startswith("/usr/local/bin:")


def test_untrusted_service_does_not_start(ctx, tmp_path):
    _mount(ctx, tmp_path, _service_manifest())
    with pytest.raises(ServiceError) as info:
        ctx.services.start(SVC)
    assert info.value.code == "NOT_TRUSTED"


def test_python_service_lifecycle_and_token_mapping(ctx, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    _mount(ctx, tmp_path, _service_manifest())
    ctx.registry.set_trust(SVC, True)
    status = ctx.services.start(SVC)
    try:
        assert status["running"] and status["ready"]
        echo = json.loads(dispatch(ctx, SVC, "service", "request", {"path": "/api/echo"})["body"])
        assert echo == {"path": "/api/echo", "ext": SVC, "hdr": SVC}
        keys = json.loads(ctx.services.request(SVC, "/api/env")["body"])["keys"]
        assert "OPENAI_API_KEY" not in keys and "PORT" in keys
        token = ctx.services._procs[SVC].token
        assert ctx.services.owner_of_token(token) == SVC
        assert ctx.services.owner_of_token("forged") is None
        with pytest.raises(ServiceError):
            ctx.services.request(SVC, "http://evil.example/")
    finally:
        assert ctx.services.stop(SVC)
    assert ctx.services.owner_of_token(token) is None
    assert not ctx.services.status(SVC)["running"]
    log = (ctx.paths.service_logs / f"{SVC}.log").read_text(encoding="utf-8")
    assert "service 啟動" in log


def test_untrust_is_enforced_on_next_start(ctx, tmp_path):
    _mount(ctx, tmp_path, _service_manifest())
    ctx.registry.set_trust(SVC, True)
    ctx.registry.set_trust(SVC, False)
    with pytest.raises(ServiceError):
        ctx.services.start(SVC)


def test_public_listen_needs_network_grant(ctx, tmp_path):
    _mount(ctx, tmp_path, _service_manifest(host="0.0.0.0"))
    ctx.registry.set_trust(SVC, True)
    with pytest.raises(ServiceError) as info:
        ctx.services.start(SVC)
    assert info.value.code == "PERMISSION_DENIED"


def test_fixed_port_in_use_refused(ctx, tmp_path):
    import socket

    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        _mount(ctx, tmp_path, _service_manifest(port=busy.getsockname()[1]))
        ctx.registry.set_trust(SVC, True)
        with pytest.raises(ServiceError) as info:
            ctx.services.start(SVC)
    assert info.value.code == "PORT_IN_USE"


def test_node_version_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(services_mod.shutil, "which", lambda name: "/usr/local/bin/node")

    class Result:
        stdout = "v22.1.0\n"

    monkeypatch.setattr(services_mod.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(ServiceError) as info:
        services_mod.node_command(tmp_path / "index.js", "24")
    assert info.value.code == "RUNTIME_TOO_OLD"
    assert services_mod.node_command(tmp_path / "index.js", "18")[0] == "/usr/local/bin/node"


@pytest.mark.parametrize(("listen_host", "connect_host"), [
    ("127.0.0.1", "127.0.0.1"),
    ("localhost", "localhost"),
    ("::1", "::1"),
    ("0.0.0.0", "127.0.0.1"),
    ("::", "::1"),
])
def test_service_connect_host_respects_ipv6_and_normalizes_wildcards(listen_host, connect_host):
    assert services_mod._connect_host(listen_host) == connect_host


def test_failed_readiness_cleans_up_process_and_token(ctx, tmp_path, monkeypatch):
    root = write_ext(
        tmp_path / "svc",
        _service_manifest(),
        {"view/index.html": HTML, "service/main.py": "import time\ntime.sleep(60)\n"},
    )
    ctx.registry.add_mount(str(root))
    ctx.registry.set_trust(SVC, True)
    monkeypatch.setattr(services_mod, "READY_TIMEOUT_S", 0.05)
    with pytest.raises(ServiceError) as info:
        ctx.services.start(SVC)
    assert info.value.code == "SERVICE_NOT_READY"
    assert SVC not in ctx.services._procs
    assert ctx.services._tokens == {}


def test_web_extension_cannot_use_service_request(ctx, tmp_path):
    root = write_ext(tmp_path / "web", web_manifest(id="com.test.web"), {"view/index.html": HTML})
    ctx.registry.add_mount(str(root))
    with pytest.raises(BridgeError) as info:
        dispatch(ctx, "com.test.web", "service", "request", {"path": "/"})
    assert info.value.code == "NOT_SERVICE"
