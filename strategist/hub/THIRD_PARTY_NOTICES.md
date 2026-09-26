# 第三方授權聲明（Randy Hub）

## x-hub

- 來源：https://github.com/dckxx/x-hub
- 參考版本：commit `8e195b92e847fabc7365426866a0273ef933f13f`（2026-09-23）
- 授權：MIT License — Copyright (c) 2026 dckxx
- 授權全文：同目錄 [`LICENSE-x-hub.txt`](LICENSE-x-hub.txt)（原文保留，未修改）

Randy Hub **沒有直接複製 x-hub 的 Rust / Vue 原始碼檔案**；以下檔案是「閱讀 x-hub 對應實作後，以 Python / 原生 JS 改寫」，
或直接沿用其介面契約（欄位名、訊息格式、CSS 令牌名）。每個檔案檔頭都有 `Adapted from x-hub` 標記與 Randy 修改摘要。

| Randy Hub 檔案 | 改寫自 x-hub | 沿用的部分 |
|---|---|---|
| `manifest.py` | `skills/x-hub-extension/references/manifest.md`、`src-tauri/src/precheck.rs` | manifest 欄位、id/version 規則、保留命名空間、network 只管對外監聽 |
| `content_server.py` | `src-tauri/src/ext_protocol.rs`、`market.rs::sensitive_package_file`、ADR 0008 | 逐段路徑校驗、機密檔名/副檔名規則、內容 CSP、HTML 注入橋腳本 |
| `capabilities.py` | `src-tauri/src/xhub_api.rs` | CAPABILITIES 靜態表 + 統一分派、runtime.info 回傳能力表 |
| `extensions.py` | `src-tauri/src/extension.rs`、ADR 0007 | 擴充掃描、權限覆蓋、service 綁定版本信任、開發目錄熱重載戳 |
| `services.py` | `src-tauri/src/service.rs`、`runtime.rs`、ADR 0007 | 動態埠、健康探測、日誌落盤（1MB + 啟動分隔線）、子行程環境白名單 |
| `providers.py` | `src/components/AiProviders.vue`、`src-tauri/src/chat.rs`、`credentials.rs` | 測試連通、拉取模型、key 存系統鑰匙圈、遠端強制 https |
| `backup.py` | `commands.rs` 備份 / 還原 | 單一 zip 打包 |
| `precheck.py` | `skills/x-hub-extension/references/debug-deploy.md`「平台關卡」 | 靜態掃描 xhub.* 呼叫對帳權限 |
| `static/xhub-bridge.js` | `src-tauri/src/extension.rs` 的 `XHUB_BRIDGE_SCRIPT` | `window.xhub` API 形狀、postMessage 訊息格式、applyTheme 令牌對照 |
| `static/js/host-bridge.js` | `src/composables/useExtensionFrame.ts` | source + origin 雙核對、theme.get 宿主回包、看門狗、熱重載 |
| `static/js/theme.js`、`static/app.css` | `src/style.css`、`src/composables/themeTokens.ts`、`DESIGN.md` | 亮/暗色設計令牌、玻璃卡（frost）、Bento 風格 |
| `static/js/dashboard.js`、`extensions.js`、`providers.js` | `useDashboardLayout.ts`、`ExtensionCenter.vue`、`AiProviders.vue` | 版面概念與操作流程（重寫） |
| `examples/security-probe/` | `tests/extensions/security-probe/` | 安全探針概念（改成自動化多項探測） |
| `skills/randy-hub-extension/`（repo 根） | `skills/x-hub-extension/` | 分步對話產生擴充的工作流程、範本結構 |

## Randy 自有模組（非改寫）

`__main__.py`、`server.py`、`api.py`、`context.py`、`db.py`、`keychain.py`、`paths.py`、`macos.py`、`strategist_adapter.py`、
`shell_macos.py`、`hotkey_macos.py`、`selftest.py`、`static/index.html`、`static/js/{main,api,store,ui,strategist,settings}.js`、
`examples/hello/`、`examples/echo-service/`、`tests/`。
