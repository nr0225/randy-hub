"""AI 供應商：OpenAI 相容呼叫、key 只進 Keychain 且介面只見遮罩、遠端強制 https。"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from strategist.hub.db import HubDB
from strategist.hub.keychain import MemoryBackend, SecretStore
from strategist.hub.providers import ProviderError, ProviderManager, check_base_url

KEY = "sk-test-abcdefghijklmnop"


@pytest.fixture()
def fake_api():
    seen = []

    class H(BaseHTTPRequestHandler):
        def _json(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self):
            seen.append(dict(self.headers))
            return self.headers.get("Authorization") == f"Bearer {KEY}"

        def do_GET(self):
            if not self._authed():
                self._json(401, {"error": "bad key"})
            elif self.path == "/v1/models":
                self._json(200, {"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]})
            else:
                self._json(404, {})

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            if not self._authed():
                self._json(401, {})
                return
            self._json(200, {"choices": [{"message": {"content": f"echo:{body['messages'][0]['content']}"}}],
                             "usage": {"total_tokens": 3}})

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1", seen
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def manager(tmp_path):
    db = HubDB(tmp_path / "hub.db")
    return ProviderManager(db, SecretStore(db, MemoryBackend()))


def test_full_flow_with_masked_key(manager, fake_api):
    url, _ = fake_api
    created = manager.create("custom", name="本機測試", base_url=url)
    assert created["hasKey"] is False
    manager.set_key(created["id"], KEY)
    assert manager.test(created["id"])["ok"] is True
    assert manager.fetch_models(created["id"]) == ["gpt-x", "gpt-y"]
    manager.update(created["id"], {"models": ["gpt-x"], "defaultModel": "gpt-x"})
    assert manager.chat(created["id"], "哈囉")["reply"] == "echo:哈囉"
    listing = json.dumps(manager.list(), ensure_ascii=False)
    assert KEY not in listing and "••••mnop" in listing
    assert KEY not in json.dumps(manager.db.recent_audit())


def test_wrong_key_gives_friendly_error(manager, fake_api):
    url, _ = fake_api
    created = manager.create("custom", base_url=url)
    manager.set_key(created["id"], "sk-wrong-key-123456")
    result = manager.test(created["id"])
    assert result["ok"] is False and result["code"] == "AUTH_FAILED"


def test_missing_key_for_cloud_preset(manager):
    created = manager.create("openai")
    with pytest.raises(ProviderError) as info:
        manager.fetch_models(created["id"])
    assert info.value.code == "KEY_MISSING"


def test_anthropic_preset_sends_native_headers(manager, fake_api):
    url, seen = fake_api
    created = manager.create("anthropic", base_url=url)
    manager.set_key(created["id"], KEY)
    manager.fetch_models(created["id"])
    headers = {k.lower(): v for k, v in seen[-1].items()}  # urllib 會把標頭名轉成首字大寫
    assert headers.get("x-api-key") == KEY and headers.get("anthropic-version")


@pytest.mark.parametrize("bad", ["http://api.example.com/v1", "https://user:pw@api.example.com/v1", "ftp://x", "not a url"])
def test_base_url_rules(bad):
    with pytest.raises(ProviderError):
        check_base_url(bad)


def test_local_http_allowed_and_presets_cover_required_vendors():
    assert check_base_url("http://localhost:11434/v1/") == "http://localhost:11434/v1"
    from strategist.hub.providers import PRESETS

    assert {"openai", "anthropic", "nim", "deepseek", "ollama", "mlx"} <= set(PRESETS)


def test_delete_removes_key(manager, fake_api):
    created = manager.create("custom", base_url=fake_api[0])
    manager.set_key(created["id"], KEY)
    manager.delete(created["id"])
    assert manager.secrets.get(f"provider:{created['id']}", "api_key") is None
