"""Hub 的 AI 供應商管理（OpenAI 相容抽象）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — src/components/AiProviders.vue、chat.rs 的做法：
測試連通、拉取模型清單勾選加入、API Key 存系統鑰匙圈、介面只顯示遮罩。
Randy 修改：
  - 內建預設組：GPT / NVIDIA NIM / DeepSeek / Ollama / MLX 走 OpenAI 相容 API；Claude 走 Anthropic Messages API
  - 遠端端點強制 https（http 只允許本機回環），避免 key 走明文
  - 介面不提供「顯示 / 複製 key」（x-hub 有），只回遮罩
  - 與 Strategist routing 解耦：這裡的設定不會改動 strategist/settings.json
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .db import HubDB
from .keychain import SecretStore, mask

KEY_NAME = "api_key"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PRESETS: dict[str, dict] = {
    "openai": {"name": "OpenAI（GPT）", "baseUrl": "https://api.openai.com/v1", "needsKey": True},
    "anthropic": {"name": "Anthropic（Claude）", "baseUrl": "https://api.anthropic.com/v1", "needsKey": True,
                  "extraHeaders": {"anthropic-version": "2023-06-01"}, "keyHeader": "x-api-key"},
    "nim": {"name": "NVIDIA NIM", "baseUrl": "https://integrate.api.nvidia.com/v1", "needsKey": True},
    "deepseek": {"name": "DeepSeek", "baseUrl": "https://api.deepseek.com/v1", "needsKey": True},
    "ollama": {"name": "Ollama（本機）", "baseUrl": "http://localhost:11434/v1", "needsKey": False},
    "mlx": {"name": "MLX（本機 mlx_lm.server）", "baseUrl": "http://localhost:8080/v1", "needsKey": False},
    "custom": {"name": "自訂 OpenAI 相容端點", "baseUrl": "", "needsKey": False},
}


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_base_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ProviderError("INVALID_ARGUMENT", "base URL 必須是 http(s)://host/...")
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        raise ProviderError("INVALID_ARGUMENT", "遠端端點必須用 https（http 只允許本機）")
    if parts.username or parts.password:
        raise ProviderError("INVALID_ARGUMENT", "base URL 不可夾帶帳密，請把 key 另外設定")
    return text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟隨轉址：urllib 預設會把 Authorization / x-api-key 一併送到新網址（含 https→http 降級）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, PLR0913
        return None


def _friendly_http_error(exc: Exception) -> ProviderError:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (301, 302, 303, 307, 308):
            return ProviderError("REDIRECT_BLOCKED", f"HTTP {exc.code}：端點要求轉址，為保護 API key 不跟隨；請把 base URL 改成最終網址")
        if exc.code in (401, 403):
            return ProviderError("AUTH_FAILED", f"HTTP {exc.code}：API key 無效或沒有權限")
        if exc.code == 404:
            return ProviderError("NOT_FOUND", "HTTP 404：端點不存在，請檢查 base URL")
        if exc.code == 429:
            return ProviderError("RATE_LIMITED", "HTTP 429：額度或頻率限制")
        return ProviderError("HTTP_ERROR", f"HTTP {exc.code}")
    if isinstance(exc, urllib.error.URLError):
        return ProviderError("UNREACHABLE", f"連不到服務：{exc.reason}（本機服務沒開？網路？）")
    return ProviderError("UNREACHABLE", f"連線失敗：{type(exc).__name__}")


class ProviderManager:
    def __init__(self, db: HubDB, secrets: SecretStore, opener=None):
        self.db = db
        self.secrets = secrets
        self._open = opener or urllib.request.build_opener(_NoRedirect).open

    # ---- CRUD ----
    def _owner(self, provider_id: str) -> str:
        return f"provider:{provider_id}"

    def _require(self, provider_id: str) -> dict:
        record = self.db.get_provider(provider_id)
        if not record:
            raise ProviderError("NOT_FOUND", f"找不到供應商：{provider_id}")
        return record

    def public(self, record: dict) -> dict:
        view = dict(record)
        needs_key = PRESETS.get(record.get("preset", ""), {}).get("needsKey", False)
        has_key = KEY_NAME in self.secrets.names(self._owner(record["id"]))
        view["keyMasked"] = mask(self.secrets.get(self._owner(record["id"]), KEY_NAME)) if has_key else ""
        view["hasKey"] = has_key
        view["needsKey"] = needs_key
        return view

    def list(self) -> list[dict]:
        return [self.public(r) for r in self.db.list_providers()]

    def create(self, preset: str, name: str | None = None, base_url: str | None = None) -> dict:
        if preset not in PRESETS:
            raise ProviderError("INVALID_ARGUMENT", f"未知的預設組：{preset}")
        spec = PRESETS[preset]
        existing = {p["id"] for p in self.db.list_providers()}
        provider_id, n = preset, 2
        while provider_id in existing:
            provider_id, n = f"{preset}-{n}", n + 1
        record = {
            "id": provider_id, "preset": preset, "name": (name or spec["name"]).strip()[:80],
            "baseUrl": check_base_url(base_url or spec["baseUrl"]), "models": [], "defaultModel": "",
            "enabled": True, "lastTest": None, "createdAt": int(time.time()),
        }
        self.db.save_provider(record)
        self.db.audit("user", "provider-create", {"id": provider_id, "preset": preset})
        return self.public(record)

    def update(self, provider_id: str, fields: dict) -> dict:
        record = self._require(provider_id)
        if "name" in fields:
            record["name"] = str(fields["name"]).strip()[:80] or record["name"]
        if "baseUrl" in fields:
            record["baseUrl"] = check_base_url(str(fields["baseUrl"]))
        if "models" in fields:
            models = fields["models"]
            if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
                raise ProviderError("INVALID_ARGUMENT", "models 必須是字串陣列")
            record["models"] = sorted(dict.fromkeys(m.strip() for m in models if m.strip()))[:500]
        if "defaultModel" in fields:
            record["defaultModel"] = str(fields["defaultModel"]).strip()[:200]
        if "enabled" in fields:
            record["enabled"] = bool(fields["enabled"])
        self.db.save_provider(record)
        return self.public(record)

    def delete(self, provider_id: str) -> None:
        self._require(provider_id)
        self.secrets.delete(self._owner(provider_id), KEY_NAME)
        self.db.delete_provider(provider_id)
        self.db.audit("user", "provider-delete", {"id": provider_id})

    def set_key(self, provider_id: str, key: str) -> dict:
        record = self._require(provider_id)
        self.secrets.set(self._owner(provider_id), KEY_NAME, key)
        self.db.audit("user", "provider-key-set", {"id": provider_id})  # 只記事件，不記值
        return self.public(record)

    def clear_key(self, provider_id: str) -> dict:
        record = self._require(provider_id)
        self.secrets.delete(self._owner(provider_id), KEY_NAME)
        self.db.audit("user", "provider-key-clear", {"id": provider_id})
        return self.public(record)

    # ---- 呼叫 OpenAI 相容端點 ----
    def _headers(self, record: dict) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        spec = PRESETS.get(record.get("preset", ""), {})
        key = self.secrets.get(self._owner(record["id"]), KEY_NAME)
        if spec.get("needsKey") and not key:
            raise ProviderError("KEY_MISSING", "尚未設定 API key")
        if key:
            if spec.get("keyHeader"):
                headers[spec["keyHeader"]] = key
            else:
                headers["Authorization"] = f"Bearer {key}"
        headers.update(spec.get("extraHeaders", {}))
        return headers

    def _call(self, record: dict, path: str, payload: dict | None, timeout: float) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(record["baseUrl"] + path, data=data, headers=self._headers(record),
                                     method="POST" if data else "GET")
        try:
            with self._open(req, timeout=timeout) as resp:
                return json.loads(resp.read(8 * 1024 * 1024).decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError) as exc:
            raise _friendly_http_error(exc) from exc
        except ValueError as exc:
            raise ProviderError("BAD_RESPONSE", "回應不是合法 JSON") from exc

    def fetch_models(self, provider_id: str) -> list[str]:
        body = self._call(self._require(provider_id), "/models", None, 15)
        items = body.get("data") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise ProviderError("BAD_RESPONSE", "回應缺少 data 陣列（不是 OpenAI 相容的 /models？）")
        return sorted({str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")})

    def test(self, provider_id: str) -> dict:
        record = self._require(provider_id)
        try:
            models = self.fetch_models(provider_id)
            result = {"ok": True, "at": int(time.time()), "modelCount": len(models)}
        except ProviderError as exc:
            result = {"ok": False, "at": int(time.time()), "code": exc.code, "error": str(exc)}
        record["lastTest"] = result
        self.db.save_provider(record)
        return result

    def chat(self, provider_id: str, prompt: str, model: str | None = None, max_tokens: int = 256) -> dict:
        record = self._require(provider_id)
        chosen = (model or record.get("defaultModel") or "").strip()
        if not chosen:
            raise ProviderError("INVALID_ARGUMENT", "請先選預設模型")
        if not re.match(r"^[\w.:/@+-]{1,200}$", chosen):
            raise ProviderError("INVALID_ARGUMENT", "模型名稱不合法")
        payload = {"model": chosen, "messages": [{"role": "user", "content": str(prompt)[:20_000]}],
                   "max_tokens": max(1, min(int(max_tokens), 4096))}
        started = time.perf_counter()
        if record.get("preset") == "anthropic":
            body = self._call(record, "/messages", payload, 120)
            try:
                blocks = body["content"]
                text = "".join(
                    str(block.get("text", ""))
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip()
            except (KeyError, TypeError) as exc:
                raise ProviderError("BAD_RESPONSE", "回應缺少 content 文字區塊") from exc
            if not text:
                raise ProviderError("BAD_RESPONSE", "回應沒有可用的文字內容")
        else:
            body = self._call(record, "/chat/completions", payload, 120)
            try:
                text = body["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise ProviderError("BAD_RESPONSE", "回應缺少 choices[0].message.content") from exc
        return {"model": chosen, "reply": text, "usage": body.get("usage"),
                "latencyMs": int((time.perf_counter() - started) * 1000)}
