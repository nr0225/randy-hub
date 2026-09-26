"""macOS 原生替代品（Randy 自有模組）。

x-hub 的 Windows 專用抽象在這裡換成 macOS 做法：
  opener / ShellExecute     → /usr/bin/open（只放行 http/https）
  自繪右下角通知窗（notify.rs）→ osascript `display notification`（參數走 argv，不拼字串）
  %USERPROFILE%\\Downloads    → ~/Downloads
  Windows Run / 計畫任務      → launchd（這裡只做唯讀查詢 launchctl print）
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

_UNSAFE_NAME = re.compile(r"[\x00-\x1f/\\:]")


def open_url(url: str) -> None:
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        raise ValueError("只允許 http/https 連結")
    subprocess.run(["/usr/bin/open", url], check=False, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title: str, body: str) -> None:
    script = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]
    subprocess.run(["/usr/bin/osascript", *script, title[:120], body[:400]], check=False, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def downloads_dir() -> Path:
    override = os.environ.get("RANDY_HUB_DOWNLOADS", "").strip()
    return Path(override).expanduser() if override else Path.home() / "Downloads"


# 雙擊就可能執行 / 自動開啟的類型：一律補 .txt（安全審查 M3）
DANGEROUS_SUFFIXES = {
    ".command", ".terminal", ".tool", ".sh", ".zsh", ".bash", ".csh", ".app", ".pkg", ".mpkg", ".dmg",
    ".webloc", ".inetloc", ".fileloc", ".url", ".scpt", ".scptd", ".applescript", ".workflow", ".action",
    ".definition", ".jar", ".py", ".pyw", ".pl", ".rb", ".mobileconfig", ".prefpane", ".kext", ".dylib", ".osax",
}


def safe_filename(name: str) -> str:
    """只留 basename；去掉控制字元與隱形格式字元（RTLO 等雙向控制可偽裝副檔名）；危險副檔名補 .txt。"""
    base = os.path.basename(str(name or "").strip())
    base = "".join(ch for ch in base if unicodedata.category(ch) not in ("Cc", "Cf"))
    base = _UNSAFE_NAME.sub("_", base).lstrip(".")[:170] or "download.txt"
    if Path(base).suffix.lower() in DANGEROUS_SUFFIXES:
        base += ".txt"
    return base


def mark_quarantine(path: Path) -> None:
    """加上 com.apple.quarantine：使用者打開時由 Gatekeeper 檢查（Python 寫的檔預設沒有這個標記）。"""
    value = f"0083;{int(time.time()):x};Randy Hub;"
    subprocess.run(["/usr/bin/xattr", "-w", "com.apple.quarantine", value, str(path)], check=False,
                   timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def write_new_file(folder: Path, name: str, data: bytes) -> Path:
    """一定建立新檔：O_EXCL|O_NOFOLLOW，不覆寫、不跟隨預先放好的符號連結；重名自動加 (n)。"""
    folder.mkdir(parents=True, exist_ok=True)
    safe = safe_filename(name)
    stem, suffix = Path(safe).stem, Path(safe).suffix
    for index in range(1000):
        candidate = folder / (safe if index == 0 else f"{stem} ({index}){suffix}")
        try:
            fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        mark_quarantine(candidate)
        return candidate
    raise OSError("找不到可用的檔名")


def launchctl_status(label: str) -> dict:
    """唯讀查詢 launchd 服務狀態（不載入、不卸載任何東西）。"""
    if not re.match(r"^[A-Za-z0-9._-]+$", label or ""):
        return {"label": label, "loaded": False, "error": "invalid label"}
    try:
        result = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"label": label, "loaded": False, "error": type(exc).__name__}
    if result.returncode != 0:
        return {"label": label, "loaded": False}
    state = re.search(r"^\s*state = (\S+)", result.stdout, re.MULTILINE)
    pid = re.search(r"^\s*pid = (\d+)", result.stdout, re.MULTILINE)
    return {"label": label, "loaded": True, "state": state.group(1) if state else "unknown",
            "pid": int(pid.group(1)) if pid else None}
