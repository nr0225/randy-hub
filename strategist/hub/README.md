# Randy Hub

軍師中控的 macOS 桌面殼 + Extension Host。擴充格式與 x-hub 相容（MIT © 2026 dckxx，見 `THIRD_PARTY_NOTICES.md`）。
Hub **不 import、不修改** Strategist：只經本機閘道 `127.0.0.1:5001` 與唯讀解析取得狀態。

## 指令（在 repo 根目錄；建議用 `.venv/bin/python`，原生視窗需要 venv 內的 pyobjc）

```
python3 -m strategist.hub              # Hub + 原生視窗（WKWebView、選單列「◆ Hub」、⌃⇧Space 顯示/隱藏）
python3 -m strategist.hub --browser    # 不用原生視窗，改用瀏覽器（Chrome app 模式優先）
python3 -m strategist.hub serve        # 只跑伺服器（印出含一次性令牌的網址）
python3 -m strategist.hub open         # 打開已在執行的 Hub
python3 -m strategist.hub doctor       # 健康檢查
python3 -m strategist.hub selftest     # MVP 驗收自測（暫存資料根 + 記憶體 Keychain，不碰正式資料）
python3 -m strategist.hub precheck DIR # 擴充預檢
python3 -m strategist.hub backup       # 備份 zip（資料庫 + 已安裝擴充；不含 Keychain）
python3 -m strategist.hub restore ZIP  # 還原（先關 Hub；舊資料留在 backups/pre-restore-*）
```

測試：`python -m pytest strategist/hub/tests -q`

## 環境變數

| 變數 | 用途 |
|---|---|
| `RANDY_HUB_HOME` | 資料根（預設 `~/Library/Application Support/RandyHub`） |
| `RANDY_STRATEGIST_ROOT` | Strategist repo 根（預設 = Hub 所在 repo；設定頁也可改並持久化） |
| `RANDY_STRATEGIST_GATEWAY` | 閘道網址（預設 `http://127.0.0.1:5001`，只允許回環） |
| `RANDY_HUB_KEYCHAIN=memory` | 測試用：機密只放記憶體，不碰真實 Keychain |

## 目錄

```
server.py / api.py        HTTP 邊界（Host/Token/Origin 檢查）與 JSON API
content_server.py         每擴充獨立 origin 的靜態內容伺服器（注入橋、CSP、擋點檔/機密檔）
capabilities.py           橋能力表 + 權限檢查（宣告 × 授予）
extensions.py / manifest.py  擴充掃描、驗證、授權、信任
services.py               service 後端托管（env 白名單、令牌、健康探測、日誌）
keychain.py               macOS Keychain（按 owner 隔離）
providers.py              Hub 的 AI 供應商（OpenAI 相容）
strategist_adapter.py     軍師中控唯讀轉接
shell_macos.py / hotkey_macos.py / macos.py   原生殼與 macOS 替代品
static/                   Hub UI（純 ES modules，零建置）與注入擴充的 xhub-bridge.js
examples/                 內建範例擴充（見 examples/README.md）
```

## 擴充身分

Hub 內部以「安裝金鑰」`<manifest id>@<來源資料夾雜湊>` 辨識擴充：授權、信任、storage、config、機密、專屬埠都綁它。
別的資料夾宣稱同 id（即使同版號）拿到的是全新空身分，並記稽核 `identity-changed`；資料夾搬家同理（搬回原路徑即恢復）。

寫擴充：用 repo 根的 `skills/randy-hub-extension/`。
