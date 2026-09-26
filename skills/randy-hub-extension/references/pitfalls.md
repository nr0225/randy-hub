# 易錯點（交付前逐條對）

> 前 9 條沿用 x-hub `references/pitfalls.md`（MIT, © 2026 dckxx）的實機踩坑；10 條以後是 Randy Hub（macOS / sandbox iframe）特有。

1. 是 Extension 不是 plugin；`id` 用自己的網域，不用 `com.randy.hub.*` / `com.x-hub.*`。
2. `entry` 是 HTML，不是 `.js`。
3. 字段大小寫：`openIn`、`minSize`、`dependsOn`、`moduleVariants` 是駝峰。
4. `requires` 寫能力名（`storage.get`），不是權限名（`strategist:read`）。
5. 不要 CDN（Tailwind / 字型 / 圖示庫都要本地化）。
6. 顏色一律 `var(--xhub-*, fallback)`，`:root` 寫亮色兜底、`:root[data-xhub-theme="dark"]` 寫暗色兜底。
7. 頁面底 `var(--xhub-page-bg, transparent)`；卡片面 `var(--xhub-surface)`；`--xhub-bg-page` 是漸層不能當顏色。
8. `xhub.storage` 是非同步的：先讀回再首繪；module / view / drawer 共用同一份，鍵名自己保證唯一。
9. module 卡片可能很矮：`height:100%` + `box-sizing:border-box` + `clamp()`，不要寫死 `min-height`。
10. **iframe 是 sandbox（無 allow-modals / popups / forms / top-navigation）**：`alert()`、`confirm()`、`window.open()`、
    `target="_blank"`、`<form>` 送出都無效。提示用頁內 UI；外部連結用 `xhub.openExternal(url)`。
11. **CSP `connect-src 'self'`**：前端 `fetch` 只能打自己的 origin（自己的靜態檔）。外部 API、Hub API、軍師閘道一律不行——需要就寫 service 後端。
12. **點檔與機密命名檔不會被送出**：`.data/`、`.env`、`secrets.json`、`*.db`、`*.pem`、`*.key`、`*.log`… 放在擴充目錄也讀不到（這是保護，不是 bug）。
13. **每個擴充一個獨立 origin（127.0.0.1:<專屬埠>）**：不要假設 origin 固定；自己的 `localStorage` 只在同一台 Mac 的同一個 Hub 資料根內穩定。跨裝置要持久的資料用 `xhub.storage`。
14. **主題跟隨**：宿主會在 `<html>` 寫 `data-xhub-theme` 並同步 `color-scheme`；自己的 CSS 不要把 `color-scheme` 寫死成 light，否則暗色宿主裡會出現不透明白底。
15. **權限被拒要講人話**：每個 `xhub.*` 呼叫都 `try/catch`，把 `err.code` 轉成使用者看得懂的提示（例如「請到擴充中心開啟 strategist:command」）。
16. **版本號一改，高危授權與後端信任都會重置**：發新版時提醒使用者要重新確認。
17. **交付前必跑** `python3 -m strategist.hub precheck <目錄>`：用到沒宣告的權限是 error（會被執行期擋掉）。
18. **擴充身分 = id ＋ 來源資料夾**：資料夾一搬家（或別人的資料夾宣稱同 id），授權、信任、`xhub.storage`、設定、機密、專屬埠全部是新的一份（舊資料還在，搬回原路徑就回來）。開發時不要頻繁換資料夾位置；要換就提醒使用者重新授權。
