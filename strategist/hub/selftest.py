"""MVP 驗收自測（Randy 自有模組）：python3 -m strategist.hub selftest

逐條對照 MVP 成功標準，用暫存資料根 + 記憶體 Keychain，不碰正式資料、不改 Strategist。
"""
from __future__ import annotations

import hashlib
import http.client
import json
import platform
import re
import tempfile
import time
from pathlib import Path

from .keychain import MemoryBackend
from .paths import HubPaths
from .server import TOKEN_HEADER, HubServer
from .strategist_adapter import ROUTE_KEYS

HELLO, PROBE, ECHO = "com.randy.hello", "com.randy.security-probe", "com.randy.echo-service"


def _http(port: int, method: str, path: str, *, token: str | None = None, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    all_headers = dict(headers or {})
    if token:
        all_headers[TOKEN_HEADER] = token
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=all_headers)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    try:
        return resp.status, json.loads(raw)
    except ValueError:
        return resp.status, raw


def _call(hub: HubServer, ext_id: str, ns: str, method: str, args=None) -> dict:
    return _http(hub.port, "POST", f"/api/ext/{ext_id}/call", token=hub.token,
                 body={"namespace": ns, "method": method, "args": args or {}})[1]


def _strategist_fingerprint(root: Path) -> str:
    digest = hashlib.sha1()
    folder = root / "strategist"
    if folder.is_dir():
        for path in sorted(folder.glob("*.py")) + sorted(folder.glob("*.json")) + sorted(folder.glob("provider/*.py")):
            digest.update(f"{path}:{path.stat().st_mtime_ns}:{path.stat().st_size};".encode())
    return digest.hexdigest()


class Report:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str) -> bool:
        self.rows.append((name, bool(ok), detail))
        return bool(ok)

    def print(self) -> int:
        width = max(len(n) for n, _, _ in self.rows)
        for name, ok, detail in self.rows:
            print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
        failed = sum(1 for _, ok, _ in self.rows if not ok)
        print(f"\n{len(self.rows) - failed}/{len(self.rows)} 通過")
        return 1 if failed else 0


def _check_boot_and_dashboard(r: Report, hub: HubServer) -> None:
    r.check("1 macOS M4 可跑", platform.system() == "Darwin" and platform.machine() == "arm64",
            f"{platform.system()} {platform.mac_ver()[0]} {platform.machine()}")
    status, state = _http(hub.port, "GET", "/api/state", token=hub.token)
    r.check("2 Randy Hub 能啟動", status == 200 and state.get("origin") == hub.origin, f"{hub.origin} v{state.get('version')}")
    ui_status, _ = _http(hub.port, "GET", "/")
    js_status, _ = _http(hub.port, "GET", "/static/js/main.js")
    ext_status, exts = _http(hub.port, "GET", "/api/extensions", token=hub.token)
    ok = ui_status == 200 and js_status == 200 and ext_status == 200
    r.check("3 Dashboard 正常（UI + API）", ok, f"UI {ui_status} / main.js {js_status} / extensions {ext_status}，{len(exts.get('items', []))} 個擴充")


def _check_extension_loaded(r: Report, hub: HubServer) -> None:
    loaded = hub.ctx.registry.get(HELLO)
    detail = "未載入"
    ok = False
    if loaded:
        status, frame = _http(hub.port, "GET", f"/api/extensions/{HELLO}/frame?surface=view", token=hub.token)
        conn = http.client.HTTPConnection("127.0.0.1", loaded.server.port, timeout=10)
        conn.request("GET", "/" + loaded.manifest.entries["view"])
        resp = conn.getresponse()
        html = resp.read().decode("utf-8")
        conn.close()
        ok = status == 200 and resp.status == 200 and "__randy_hub__/bridge.js" in html and frame["origin"] != hub.origin
        detail = f"{HELLO} v{loaded.manifest.version}，獨立 origin {frame.get('origin')}，橋已注入"
    r.check("4 能載入測試 Extension", ok, detail)


def _check_isolation(r: Report, hub: HubServer) -> None:
    denied_shared = _call(hub, HELLO, "sharedStorage", "get", {"key": "x"})
    denied_cmd = _call(hub, HELLO, "strategist", "command", {"command": "selftest 不該被執行"})
    _call(hub, HELLO, "storage", "set", {"key": "counter", "value": 41})
    cross = _call(hub, PROBE, "storage", "get", {"key": "counter"})
    ext_origin = hub.ctx.registry.get(HELLO).server.origin
    api_from_ext, _ = _http(hub.port, "GET", "/api/state", token=hub.token, headers={"Origin": ext_origin})
    loaded = hub.ctx.registry.get(HELLO)
    conn = http.client.HTTPConnection("127.0.0.1", loaded.server.port, timeout=10)
    conn.request("GET", "/.storage.json")
    dot_status = conn.getresponse().status
    conn.close()
    svc_status, _ = _http(hub.port, "GET", "/svc-api/secrets/DEMO_TOKEN", headers={"Authorization": "Bearer forged"})
    checks = {
        "未宣告權限被擋": denied_shared.get("error", {}).get("code") == "PERMISSION_DENIED",
        "高危權限預設關": denied_cmd.get("error", {}).get("code") == "PERMISSION_DENIED",
        "storage 互不可見": cross.get("ok") and cross.get("data") is None,
        "擴充 origin 打 Hub API 被拒": api_from_ext == 403,
        "點檔不外流": dot_status == 403,
        "偽造 service 令牌被拒": svc_status == 401,
    }
    r.check("5 Extension 權限隔離有效", all(checks.values()), "、".join(f"{k}{'✓' if v else '✗'}" for k, v in checks.items()))


def _check_service(r: Report, hub: HubServer) -> None:
    hub.ctx.ext_secrets(ECHO).set("DEMO_TOKEN", "selftest-demo-value")
    hub.ctx.ext_secrets(HELLO).set("DEMO_TOKEN", "hello-should-stay-private")
    untrusted = _http(hub.port, "POST", f"/api/extensions/{ECHO}/service/start", token=hub.token)[0]
    hub.ctx.registry.set_trust(ECHO, True)
    started, _ = _http(hub.port, "POST", f"/api/extensions/{ECHO}/service/start", token=hub.token)
    echo = _call(hub, ECHO, "service", "request", {"path": "/api/echo?msg=hi"})
    secret = _call(hub, ECHO, "service", "request", {"path": "/api/secret-check"})
    stopped, _ = _http(hub.port, "POST", f"/api/extensions/{ECHO}/service/stop", token=hub.token)
    echo_body = json.loads(echo.get("data", {}).get("body", "{}") or "{}")
    secret_body = json.loads(secret.get("data", {}).get("body", "{}") or "{}")
    ok = untrusted == 409 and started == 200 and echo_body.get("echo") == "hi" and stopped == 200 \
        and secret_body.get("length") == len("selftest-demo-value")
    r.check("＋ background service（信任閘門 / 代轉 / 機密隔離）", ok,
            f"未信任啟動 {untrusted}、信任後 {started}、echo={echo_body.get('echo')}、取回自己機密長度 {secret_body.get('length')}、停止 {stopped}")


def _check_strategist(r: Report, hub: HubServer) -> str:
    status = hub.ctx.strategist.status()
    slots = status["providers"].get("slots", [])
    gw = status["gateway"]
    route = status["route"].get("route", {}) if status["route"].get("ok") else {}
    ok = bool(slots) or gw.get("ok")
    r.check("6 看得到現有 Strategist provider / service", ok,
            f"root={status['root']}；插槽 {len(slots)} 個；閘道 {'正常' if gw.get('ok') else '未回應'}；"
            f"目前 provider={route.get('STRATEGIST_PROVIDER', '—')}")
    values = _secret_values(hub.ctx.strategist.root)
    dumped = json.dumps(status, ensure_ascii=False)
    leaked = [v for v in values if v in dumped]
    r.check("  └ 狀態不含任何 key 值", not leaked, f"比對了 settings.json 裡 {len(values)} 個機密值，外洩 {len(leaked)} 個")
    return _strategist_fingerprint(hub.ctx.strategist.root)


def _secret_values(root: Path) -> list[str]:
    """只在本行程記憶體裡讀出來做比對，不印、不存。"""
    try:
        data = json.loads((root / "strategist" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [v for k, v in data.items() if isinstance(v, str) and len(v) >= 8 and k not in ROUTE_KEYS
            and re.search(r"KEY|TOKEN|SECRET|PASSWORD", k, re.IGNORECASE)]  # 白名單欄位（如 *_KEY_SOURCE）不是機密


def run_selftest() -> int:
    report = Report()
    with tempfile.TemporaryDirectory(prefix="randy-hub-selftest-") as tmp:
        paths = HubPaths(Path(tmp) / "home").ensure()
        keychain = MemoryBackend()
        hub = HubServer(paths, port=0, secret_backend=keychain).start_background()
        try:
            before = _strategist_fingerprint(hub.ctx.strategist.root)
            _check_boot_and_dashboard(report, hub)
            _check_extension_loaded(report, hub)
            _check_isolation(report, hub)
            _check_service(report, hub)
            after = _check_strategist(report, hub)
            _http(hub.port, "PUT", "/api/settings", token=hub.token, body={"theme.mode": "dark", "dashboard.layout": [{"id": "strategist", "hidden": False}]})
            hub.ctx.registry.set_grant(HELLO, "strategist:command", True)
        finally:
            hub.shutdown()
        time.sleep(0.2)
        again = HubServer(paths, port=0, secret_backend=keychain).start_background()
        try:
            _, state = _http(again.port, "GET", "/api/state", token=again.token)
            ok = state["settings"]["theme.mode"] == "dark" and again.ctx.registry.is_granted(HELLO, "strategist:command") \
                and again.ctx.registry.is_trusted(ECHO)
            report.check("7 重開 App 不掉設定", ok, "主題 / 版面 / 授權 / 信任 在新行程物件中都還在")
            health = again.ctx.strategist.health()
        finally:
            again.shutdown()
        report.check("8 不破壞現有 Strategist", before == after,
                     f"strategist/ 檔案指紋前後一致；閘道 {'仍正常' if health.get('ok') else '未回應（本來就沒開也算未變動）'}")
    return report.print()
