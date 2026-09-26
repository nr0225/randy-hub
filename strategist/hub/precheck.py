"""擴充預檢（交付 / 掛載前的靜態對帳）。

Adapted from x-hub (MIT, Copyright (c) 2026 dckxx) — skills/x-hub-extension/references/debug-deploy.md
「平台關卡會查什麼」：靜態掃描 .html/.js/.mjs/.cjs（跳過點檔與 node_modules），把 xhub.* 呼叫鏈反推成
所需權限，與 manifest.permissions 對帳：用到沒宣告 = error；宣告沒用到 = warn（network 例外）。
Randy 修改：權限對照直接取 Hub 的真實能力表（capabilities.capability_table），而非另寫一份規則。
"""
from __future__ import annotations

import re
from pathlib import Path

from .capabilities import capability_table
from .manifest import ManifestError, load_manifest

_SCAN_SUFFIXES = {".html", ".htm", ".js", ".mjs", ".cjs"}
_CALL = re.compile(r"\b(?:xhub|randyHub)\.([A-Za-z]+)\.([A-Za-z][\w.]*?)\s*\(")
_HOST_SIDE = {"events.on", "events.off", "runtime.open"}  # 宿主前端處理，不經能力表
_MAX_FILE_BYTES = 2 * 1024 * 1024


def _source_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).parts
        if any(p.startswith(".") or p == "node_modules" for p in rel):
            continue
        if path.is_file() and path.suffix.lower() in _SCAN_SUFFIXES and path.stat().st_size <= _MAX_FILE_BYTES:
            yield path


def scan_calls(root: Path) -> dict[str, list[str]]:
    """回傳 {"namespace.method": [出現的檔案...]}。"""
    found: dict[str, list[str]] = {}
    for path in _source_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for ns, method in _CALL.findall(text):
            found.setdefault(f"{ns}.{method}", []).append(str(path.relative_to(root)))
    return found


def precheck(root: Path) -> dict:
    root = Path(root)
    try:
        manifest = load_manifest(root)
    except ManifestError as exc:
        return {"ok": False, "errors": exc.errors, "warnings": [], "calls": {}}
    table = {f"{c['namespace']}.{c['method']}": c["permission"] for c in capability_table()}
    calls = scan_calls(root)
    errors: list[str] = []
    warnings: list[str] = list(manifest.warnings)
    needed: set[str] = set()
    for name, files in sorted(calls.items()):
        if name in _HOST_SIDE:
            continue
        if name not in table:
            warnings.append(f"{name}：Randy Hub 沒有提供這個能力（{files[0]}），執行時會回 CAPABILITY_UNAVAILABLE")
            continue
        if table[name]:
            needed.add(table[name])
    declared = set(manifest.permissions)
    for perm in sorted(needed - declared):
        errors.append(f"程式碼用到需要 {perm} 的呼叫，但 manifest.permissions 沒宣告")
    for perm in sorted(declared - needed - {"network"}):
        warnings.append(f"宣告了 {perm} 但程式碼沒用到（多宣告 = 多一分風險）")
    return {"ok": not errors, "id": manifest.id, "errors": errors, "warnings": warnings, "calls": sorted(calls)}
