# service 後端（runtime: "service"）

> 改寫自 x-hub `references/service.md`（MIT, © 2026 dckxx）；Randy 版以 Python 為主。

## 宿主給後端的環境變數（**只有這些**，其餘一律不繼承）

`PORT`、`XHUB_EXT_ID` / `RANDY_HUB_EXT_ID`、`XHUB_LISTEN_HOST`、`RANDY_HUB_URL`、`RANDY_HUB_SERVICE_TOKEN`，
加上 `HOME/USER/LANG/TMPDIR/TZ/SHELL` 與最小 `PATH`。宿主的 API key、雲端憑證、`NODE_OPTIONS`、`PYTHONPATH` 都**不會**傳進來。

## 最小後端（Python 標準庫）

見 `templates/service_main.py`。鐵律：

1. 只聽 `127.0.0.1`，埠讀 `PORT`。
2. 提供 `health` 路徑（例如 `/healthz` 回 200）。
3. 前端呼叫後端一律走 `xhub.service.request('/api/x')`（Hub 代轉，自動帶 `X-Randy-Hub-Ext` 標頭）；**不要**讓前端直接打埠（CSP 會擋）。
4. 資料 / 快取 / 日誌寫在擴充目錄的 `.data/`（點開頭，不會被 Hub 當內容送出、預檢也跳過）。
5. 不要依賴本機 `node_modules` / 第三方套件（Python 只用標準庫，或把依賴一起放進擴充目錄並在交付報告講清楚）。

## 機密（API key）

使用者在「擴充中心 → 你的擴充 → 機密」輸入，值只進 macOS Keychain（`com.randy.hub` / `ext:<擴充 id>/<名稱>`）。後端這樣取：

```python
req = urllib.request.Request(os.environ["RANDY_HUB_URL"] + "/svc-api/secrets/OPENAI_KEY",
                             headers={"Authorization": f"Bearer {os.environ['RANDY_HUB_SERVICE_TOKEN']}"})
```

- 令牌決定身分：只拿得到**自己**擴充的機密；後端停止後令牌立即失效。
- 不要把機密回傳給前端、不要寫進日誌、不要寫進 `.data/`。

## 生命週期

- **預設不啟動**：使用者要在擴充中心勾「信任此版本」→ 按「啟動後端」。版本號一改，信任自動作廢。
- Hub 結束（⌘Q / Ctrl+C / SIGTERM）會一併停掉後端（整個行程群組）。
- 日誌：`~/Library/Application Support/RandyHub/logs/service/<擴充 id>.log`（單份 1MB，每次啟動有分隔線）。
  `runtime.info().serviceReady === false` 只代表「探活沒過」，先看這個日誌，別猜。
