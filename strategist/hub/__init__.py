"""Randy Hub — macOS 桌面殼 + Extension Host（軍師中控的收編模組）。

定位：把開源專案 x-hub（MIT, Copyright (c) 2026 dckxx, https://github.com/dckxx/x-hub）
當成「可拆解的桌面殼 / Plugin Host」，吸收其擴充契約（manifest、權限、window.xhub 橋、
service 信任模型、內容來源隔離）與設計，落地成 Randy 自有的 Python 實作。

- 不取代 Strategist：只透過本機閘道（HTTP）與唯讀解析接入，不 import、不修改 Strategist。
- 來源標記：凡改寫自 x-hub 的檔案，檔頭註明「Adapted from x-hub」；其餘為 Randy 自有模組。
  完整授權聲明見 strategist/hub/THIRD_PARTY_NOTICES.md。
"""

__version__ = "0.1.0"
HOST_NAME = "randy-hub"
