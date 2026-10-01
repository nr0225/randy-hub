"""Strategist 轉接層：唯讀、不執行 Strategist 程式碼、機密只回「有沒有」、閘道限本機。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from strategist.hub.strategist_adapter import StrategistAdapter, StrategistError

ROUTER_PY = '''
raise SystemExit("router.py 被執行了——Hub 不該執行 Strategist 程式碼")
_SLOTS = [
    {"name": "mock", "type": "假回覆", "status": "ok", "note": "無"},
    {"name": "nim", "type": "雲端", "status": "key", "note": "NIM_API_KEY"},
]
'''
SETTINGS = {
    "STRATEGIST_PROVIDER": "nim",
    "STRATEGIST_MODEL": "meta/llama-3.3-70b-instruct",
    "NIM_API_KEY": "fixture_nim_primary",
    "NIM_API_KEY_FLASH": "fixture_nim_flash",
    "GEMINI_API_KEY": "",
    "OLLAMA_HOST": "http://user:fixture_password@localhost:11434",
    "STRATEGIST_CLI_CMD": "curl -H 'Authorization: fixture_token'",
    "__profiles__": {"work": {"NIM_API_KEY": "fixture_nim_profile"}},
}


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "strategist" / "provider").mkdir(parents=True)
    (tmp_path / "strategist" / "provider" / "router.py").write_text(ROUTER_PY, encoding="utf-8")
    (tmp_path / "strategist" / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def gateway():
    seen = []

    class H(BaseHTTPRequestHandler):
        def _json(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._json(200, {"ok": True, "service": "strategist-web-gateway"})

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self._json(200, {"success": True, "reply": "收到", "message": "收到", "target": "nim"})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()
    srv.server_close()


def test_slots_parsed_without_executing_strategist(root):
    result = StrategistAdapter(root).provider_slots()
    assert result["ok"] and [s["name"] for s in result["slots"]] == ["mock", "nim"]


def test_route_is_sanitized(root):
    route = StrategistAdapter(root).active_route()
    assert route["route"]["STRATEGIST_PROVIDER"] == "nim"
    assert route["keysPresent"] == {"NIM_API_KEY": True, "NIM_API_KEY_FLASH": True, "GEMINI_API_KEY": False}
    assert route["route"]["OLLAMA_HOST"] == "http://localhost:11434"
    assert "SUPERSECRET" not in json.dumps(route)


def test_keys_moved_to_keychain_still_count_as_present(root):
    path = root / "strategist" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("NIM_API_KEY")
    data["__keychain__"] = {"version": 1, "keys": ["NIM_API_KEY", "GEMINI_API_KEY"], "rev": "abc"}
    path.write_text(json.dumps(data), encoding="utf-8")
    route = StrategistAdapter(root).active_route()
    assert route["keysPresent"] == {"NIM_API_KEY": True, "NIM_API_KEY_FLASH": True, "GEMINI_API_KEY": True}
    assert "__keychain__" not in json.dumps(route)


def test_status_never_contains_secrets(root, gateway):
    url, _ = gateway
    status = StrategistAdapter(root, url).status()
    assert status["gateway"]["ok"] is True
    assert "SUPERSECRET" not in json.dumps(status, ensure_ascii=False)


def test_command_goes_through_gateway(root, gateway):
    url, seen = gateway
    result = StrategistAdapter(root, url).command("幫我看今天待辦", target="nim")
    assert result["success"] is True and result["reply"] == "收到"
    assert seen == [{"command": "幫我看今天待辦", "target": "nim", "context": ""}]


@pytest.mark.parametrize("target", ["agent", "coder", "flash", "pro"])
def test_command_accepts_worker_targets(root, gateway, target):
    url, seen = gateway
    StrategistAdapter(root, url).command("x", target=target)
    assert seen[-1]["target"] == target


def test_command_rejects_bad_target_and_empty(root, gateway):
    adapter = StrategistAdapter(root, gateway[0])
    with pytest.raises(StrategistError):
        adapter.command("x", target="rm -rf")
    with pytest.raises(StrategistError):
        adapter.command("   ")


@pytest.mark.parametrize("url", ["http://10.0.0.5:5001", "https://example.com", "ftp://127.0.0.1:21"])
def test_gateway_must_be_loopback(root, url):
    with pytest.raises(StrategistError):
        StrategistAdapter(root, url)


def test_unreachable_gateway_reports_not_raises(root):
    health = StrategistAdapter(root, "http://127.0.0.1:9").health()
    assert health["ok"] is False and "error" in health


def test_routes_pass_only_whitelisted_fields(root):
    """閘道回的路由表就算夾帶多餘欄位（例如 key 值），Hub 也只放行 route / provider / keySlot / keySet / model。"""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            rows = [{"route": "flash", "provider": "nim", "keySlot": "NIM_API_KEY_FLASH", "keySet": True,
                     "model": "m-flash", "value": "fixture_hidden_value", "debug": {"key": "fixture_hidden_value"}}]
            body = {"success": True, "routes": rows} if self.path == "/api/strategist/routes" else {"ok": True}
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        adapter = StrategistAdapter(root, f"http://127.0.0.1:{srv.server_address[1]}")
        assert adapter.routes() == {"ok": True, "routes": [
            {"route": "flash", "provider": "nim", "keySlot": "NIM_API_KEY_FLASH", "keySet": True, "model": "m-flash"}]}
        assert "SUPERSECRET" not in json.dumps(adapter.status()) + json.dumps(adapter.public_status())
    finally:
        srv.shutdown()
        srv.server_close()
