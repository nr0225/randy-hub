"""Randy Hub 命令列入口（Randy 自有模組）。

用法（在 repo 根目錄）：
  python3 -m strategist.hub              # 啟動 Hub + 原生視窗（WKWebView）；不支援時退回瀏覽器
  python3 -m strategist.hub serve        # 無視窗，只跑伺服器（印出含令牌的 UI 網址）
  python3 -m strategist.hub open         # 用瀏覽器打開已在執行的 Hub
  python3 -m strategist.hub doctor       # 健康檢查
  python3 -m strategist.hub backup       # 建立備份 zip
  python3 -m strategist.hub restore ZIP  # 從備份還原（Hub 必須先關閉）
  python3 -m strategist.hub selftest     # MVP 驗收自測（用暫存資料根，不碰正式資料）
  python3 -m strategist.hub precheck DIR # 擴充預檢（manifest 驗證 + 權限對帳）
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import __version__
from .paths import hub_paths
from .server import DEFAULT_PORT, TOKEN_HEADER, HubServer


def running_hub(paths) -> dict | None:
    """回傳正在執行的 Hub 資訊（pid 活著且令牌檔在）；否則 None。"""
    try:
        info = json.loads(paths.run_info.read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        info["token"] = paths.token_file.read_text(encoding="utf-8").strip()
        return info
    except (OSError, ValueError, KeyError):
        return None


def _hub_request(info: dict, method: str, path: str) -> dict:
    req = urllib.request.Request(info["origin"] + path, method=method, headers={TOKEN_HEADER: info["token"]})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def open_in_browser(url: str) -> None:
    chrome = Path("/Applications/Google Chrome.app")
    if chrome.exists():  # Chrome app 模式 = 無網址列的獨立視窗；另開設定檔，不碰使用者主要瀏覽資料
        profile = hub_paths().root / "browser-profile"
        subprocess.Popen(["/usr/bin/open", "-na", str(chrome), "--args", f"--app={url}", f"--user-data-dir={profile}"])
    else:
        subprocess.Popen(["/usr/bin/open", url])


def cmd_app(args) -> int:
    paths = hub_paths()
    existing = running_hub(paths)
    if existing:
        print(f"Randy Hub 已在執行（pid {existing['pid']}），直接打開它。")
        open_in_browser(f"{existing['origin']}/#token={existing['token']}")
        return 0
    from .shell_macos import run_native, webkit_available

    if args.browser or not webkit_available():
        if not args.browser:
            print("找不到可用的 WKWebView（pyobjc），改用瀏覽器開啟。")
        return cmd_serve(args, open_browser=True)
    server = HubServer(paths, port=args.port, native_shell=True).start_background()
    print(f"Randy Hub v{__version__} 啟動：{server.origin}（原生視窗）")
    run_native(server, tray=not args.no_tray, hotkey=not args.no_hotkey, inspectable=args.inspect)
    return 0


def _raise_interrupt(*_args) -> None:
    raise KeyboardInterrupt


def cmd_serve(args, open_browser: bool = False) -> int:
    paths = hub_paths()
    if running_hub(paths):
        print("Randy Hub 已在執行；要打開畫面請用：python3 -m strategist.hub open", file=sys.stderr)
        return 1
    server = HubServer(paths, port=args.port)
    print(f"Randy Hub v{__version__} 啟動：{server.origin}", flush=True)
    print(f"UI（含一次性令牌，勿外傳）：{server.ui_url}", flush=True)
    if open_browser:
        open_in_browser(server.ui_url)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        print("Randy Hub 已關閉")
    return 0


def cmd_open(_args) -> int:
    info = running_hub(hub_paths())
    if not info:
        print("Randy Hub 沒有在執行。先執行：python3 -m strategist.hub", file=sys.stderr)
        return 1
    open_in_browser(f"{info['origin']}/#token={info['token']}")
    return 0


def cmd_doctor(_args) -> int:
    info = running_hub(hub_paths())
    if not info:
        print("Randy Hub 沒有在執行；以下只做本機環境檢查。")
        from .shell_macos import webkit_available

        print(f"  Python {sys.version.split()[0]}  平台 {sys.platform}  WKWebView {'可用' if webkit_available() else '不可用'}")
        return 0
    for check in _hub_request(info, "GET", "/api/doctor")["checks"]:
        print(f"  {'✓' if check['ok'] else '✗'} {check['name']}：{check['detail']}")
    return 0


def cmd_backup(_args) -> int:
    paths = hub_paths()
    info = running_hub(paths)
    if info:
        print(_hub_request(info, "POST", "/api/backup")["path"])
        return 0
    from .backup import create_backup
    from .context import build_context

    ctx = build_context(paths, "http://127.0.0.1:0", examples=False)
    try:
        print(create_backup(ctx))
    finally:
        ctx.shutdown()
    return 0


def cmd_restore(args) -> int:
    paths = hub_paths()
    if running_hub(paths):
        print("請先關閉 Randy Hub 再還原。", file=sys.stderr)
        return 1
    from .backup import restore_backup

    result = restore_backup(paths, Path(args.zip).expanduser())
    print(f"已還原（備份時間 {result['createdAt']}）；原資料保留在 {result['rollback']}")
    print(f"為了安全，已作廢 {result['clearedGrants']} 個授權、{result['clearedTrust']} 個後端信任；請在擴充中心重新確認。")
    for mount in result["removedMounts"]:
        print(f"  需要重新掛載：{mount}")
    if result["droppedSettings"]:
        print(f"  丟棄的不合法設定：{', '.join(result['droppedSettings'])}")
    return 0


def cmd_selftest(_args) -> int:
    from .selftest import run_selftest

    return run_selftest()


def cmd_precheck(args) -> int:
    from .precheck import precheck

    report = precheck(Path(args.dir).expanduser())
    for line in report["errors"]:
        print(f"  ✗ error  {line}")
    for line in report["warnings"]:
        print(f"  ! warn   {line}")
    print(f"{'通過' if report['ok'] else '未通過'}：{report.get('id', args.dir)}（{len(report['errors'])} error / {len(report['warnings'])} warn）")
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m strategist.hub", description="Randy Hub — 軍師中控桌面殼 + Extension Host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--browser", action="store_true", help="不用原生視窗，改用瀏覽器")
    parser.add_argument("--no-tray", action="store_true")
    parser.add_argument("--no-hotkey", action="store_true")
    parser.add_argument("--inspect", action="store_true", help="允許 Safari 網頁檢閱器除錯")
    sub = parser.add_subparsers(dest="command")
    for name in ("serve", "open", "doctor", "backup", "selftest"):
        sub.add_parser(name)
    restore = sub.add_parser("restore")
    restore.add_argument("zip")
    check = sub.add_parser("precheck", help="靜態預檢擴充目錄（manifest + 權限對帳）")
    check.add_argument("dir")
    args = parser.parse_args(argv)
    handlers = {None: cmd_app, "serve": cmd_serve, "open": cmd_open, "doctor": cmd_doctor,
                "backup": cmd_backup, "restore": cmd_restore, "selftest": cmd_selftest, "precheck": cmd_precheck}
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
