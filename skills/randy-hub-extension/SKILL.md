---
name: randy-hub-extension
description: "為 Randy Hub（軍師中控的 macOS 桌面殼）產生 Extension：manifest.json + 入口 HTML + 選配的 Python/Node 後端，遵守 window.xhub 橋 API、權限風險分級與每擴充獨立 origin。使用者說「做一個 Randy Hub 擴充 / Hub 外掛 / 把這個網頁改成擴充」時使用。"
---

# Randy Hub 擴充開發

> 改寫自 x-hub 的 `skills/x-hub-extension`（MIT, Copyright (c) 2026 dckxx, github.com/dckxx/x-hub）。
> 擴充格式與 x-hub 相容（manifest 欄位、`window.xhub` 橋），差異在「權限風險分級、macOS 路徑、Python 後端、軍師中控能力」。

**術語：叫 Extension（擴充），不叫 plugin。** 擴充 = `manifest.json` + 入口 HTML（+ 選配後端）。

## 按需讀取

| 檔案 | 什麼時候讀 |
|---|---|
| `references/manifest.md` | 寫 manifest 時（欄位、id/version 規則、風險分級） |
| `references/bridge-api.md` | 要用任何 `window.xhub.*` 前——先確認能力存在、要什麼權限 |
| `references/service.md` | 需要後端 / 呼叫外部 API / AI / 機密時 |
| `references/pitfalls.md` | 寫 code 前掃一眼、交付前逐條對 |
| `templates/` | 生骨架時直接複製改欄位 |

權威來源順序：本 skill → 執行中 Hub 的 `await xhub.runtime.info()`（`capabilities` 是真實能力表）→ `strategist/hub/capabilities.py`。
查不到就**不要猜著寫**：用能力探測優雅降級，或直接告訴使用者「這條我確認不了」。

## 流程（一步一步來，不要一口氣生一整套）

**提問規則**：只問「缺的」且「會改變後續決策的」；一次最多 2 題、每題 2–3 個選項＋推薦；能推斷就改成一句聲明；
絕不問配色、字體、函式怎麼拆。用 `AskUserQuestion` 問；全程通常 2–4 輪。

1. **需求**：這個擴充幫使用者做什麼？資料從哪來？
   - 只存擴充自己 → `xhub.storage`（免權限）
   - 要看軍師中控狀態 → `strategist:read`（中風險）；要**代使用者下指令** → `strategist:command`（高危，預設關）
   - 外部網站 / API / AI / 需要 key → **service 後端**（後端自己連網不需權限；key 由使用者在擴充中心存進 Keychain，後端用令牌取回）
2. **運行時**：純前端 `web`；需要後端 `service`（**預設用 Python**，`engine.type: "python"`，只用標準庫；Node 也支援但本機要有 node）。
3. **形態（多選一次問完）**：`module`（儀表板卡片）/ `view`（完整頁面，最常見）/ `drawer`（右側抽屜）/ `window`（目前以抽屜呈現）。`drawer`/`window` 與 `view` 共用入口。
4. **權限（反推，不要問「你要什麼權限」）**：生成**之前**用一句話列出每項權限與用途，高危項要特別說明它預設是關的、使用者得自己去擴充中心打開。
5. **id**：必須問（建議 `com.<使用者網域或名字>.<短名>`）；`com.randy.hub.*`、`com.x-hub.*` 是保留命名空間，不要用。小寫、含 `.`、只允許 `a-z0-9._-`、≤128。`version` 必須 `x.y.z`。
6. **生成**：從 `templates/` 複製改欄位；每個宣告的 entry 都要有真實檔案；不要 CDN；顏色全用 `var(--xhub-*, fallback)`。
7. **預檢**（必做）：`python3 -m strategist.hub precheck <擴充目錄>` → 必須 0 error（用到沒宣告的權限＝error）。
8. **交給使用者真機跑**：
   > 1. 開 Randy Hub（`python3 -m strategist.hub`）→「擴充中心」→「我的擴充」貼上擴充資料夾的**絕對路徑** →「掛載」
   > 2. 掛上就載入；改檔約 1.5 秒自動重載；高危權限要自己在權限表勾；service 後端要勾「信任此版本」再按「啟動後端」
9. **交付報告（缺一不可）**：檔案清單、運行時與形態的理由、宣告的權限與用途、上面那段掛載指引、哪些是佔位（名字/圖示/示例資料）。

## 最容易錯的五件事

1. `entry` 的值是 **HTML 檔**，不是 `.js`。
2. 頁面底用 `var(--xhub-page-bg, transparent)`，卡片面用 `var(--xhub-surface)`；`module` 卡片保持透明。
3. 沒宣告就呼叫 → `PERMISSION_DENIED`；宣告了高危權限也要等使用者授權才生效——介面要把錯誤講人話，不能白屏。
4. iframe 是 sandbox：`alert/confirm`、`window.open`、`target=_blank`、表單送出**都不能用**；開外部連結用 `xhub.openExternal(url)`。
5. 前端**不能**直接 `fetch` 任何外部或本機埠（CSP `connect-src 'self'`）；要打後端用 `xhub.service.request('/api/x')`。
