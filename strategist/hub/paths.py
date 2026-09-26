"""Randy Hub 資料根目錄（macOS 版）。

x-hub 在 Windows 用 `%APPDATA%\\x-hub`，便攜版用 `exe\\data`。macOS 對應為
`~/Library/Application Support/RandyHub`；`RANDY_HUB_HOME` 環境變數可整個改掉（測試 / 便攜模式）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "RandyHub"
HOME_ENV = "RANDY_HUB_HOME"


@dataclass(frozen=True)
class HubPaths:
    root: Path

    @property
    def db(self) -> Path:
        return self.root / "hub.db"

    @property
    def extensions(self) -> Path:
        return self.root / "extensions"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def service_logs(self) -> Path:
        return self.root / "logs" / "service"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def run(self) -> Path:
        return self.root / "run"

    @property
    def run_info(self) -> Path:
        return self.run / "hub.json"

    @property
    def token_file(self) -> Path:
        return self.run / "hub.token"

    def ensure(self) -> "HubPaths":
        for folder in (self.root, self.extensions, self.logs, self.service_logs, self.backups, self.run):
            folder.mkdir(parents=True, exist_ok=True)
            try:
                folder.chmod(0o700)
            except OSError:
                pass
        return self


def default_root() -> Path:
    override = os.environ.get(HOME_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / "Library" / "Application Support" / APP_DIR_NAME


def hub_paths(root: Path | None = None) -> HubPaths:
    return HubPaths(root=(root or default_root())).ensure()


def repo_root() -> Path:
    """Hub 所在 repo 根目錄（strategist/hub/ 往上兩層）。"""
    return Path(__file__).resolve().parents[2]


def write_private(path: Path, text: str) -> None:
    """寫入僅擁有者可讀寫的檔案（0600）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
