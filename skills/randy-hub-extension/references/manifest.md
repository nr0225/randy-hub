# manifest.json（Randy Hub 版）

> 改寫自 x-hub `references/manifest.md`（MIT, © 2026 dckxx）。欄位名區分大小寫，必須是合法 JSON（不能有註解）。
> 驗證實作：`strategist/hub/manifest.py`；交付前跑 `python3 -m strategist.hub precheck <目錄>`。

## 必填

| 欄位 | 規則 |
|---|---|
| `id` | 小寫反向網域，**必須含 `.`**，只允許 `a-z0-9._-`，≤128，不以 `.` 開頭/結尾，不含 `..`。`com.randy.hub.*` / `com.x-hub.*` 保留 |
| `name` | 顯示名稱 |
| `version` | 必須 `x.y.z` 三段純數字（`1.0`、`v1.0.0`、`1.0.0-beta` 都不合法）。**版本一變，高危授權與 service 信任都會被重置** |

## 運行時、形態、入口

| 欄位 | 說明 |
|---|---|
| `runtime` | `web`（預設）/ `service` |
| `kind` | `module` / `view`（預設）/ `window` / `drawer` |
| `surfaces` | 宣告的形態陣列（省略 = `[kind]`） |
| `entry` | `{ 形態: "相對路徑.html" }`；每個宣告的形態都要有檔案；`window`/`drawer` 沒寫會共用 `view` 的入口；`module` 必須有自己的入口 |
| `icon` | 相對路徑 svg/png/jpg/webp/ico |
| `description` / `openIn` / `minSize` | 同 x-hub |
| `moduleVariants` / `moduleOptions` | 同 x-hub（`defaultHideTitle: false` 才顯示宿主卡片標題） |

路徑一律相對擴充目錄；**不可**用絕對路徑、`..`、反斜線、點檔（`.data/` 之類）當入口；符號連結指到目錄外會被拒。

## 權限 `permissions`（風險分級是 Randy Hub 新增的）

| 權限 | 風險 | 預設 | 用途 |
|---|---|---|---|
| `events` | low | 開 | 廣播自訂事件 |
| `notify` | low | 開 | macOS 系統通知 |
| `shared-storage` | medium | 開 | 跨擴充共享儲存 |
| `fs` | medium | 開 | 存檔到「下載」 |
| `strategist:read` | medium | 開 | 讀軍師中控狀態（不含 key） |
| `strategist:command` | **high** | **關** | 代使用者向軍師下指令（會真的執行、可能花額度） |
| `network` | **high** | **關** | service 後端對外監聽（非 127.0.0.1） |
| `data:read` / `data:write` / `clipboard` / `system` / `ai` | — | — | x-hub 相容保留或尚未開放；宣告了也拿不到能力 |

「預設開」只對**有宣告**的權限成立；使用者可在擴充中心隨時關。高危的要使用者自己勾。

## service 專屬 `backend`

```json
"backend": {
  "entry": "./service/main.py",
  "engine": { "type": "python" },
  "cwd": "./service",
  "port": 0,
  "health": "/healthz"
}
```

- `engine.type`：`python`（推薦，用 Hub 自己的 Python，以 `-E -s -B` 啟動）或 `node`（本機要有 node；`minVersion` 不夠會拒絕啟動，**不會自動下載**）。
- `port: 0` = 動態分配（推薦）；`host` 省略 = 只聽 127.0.0.1；寫 `0.0.0.0` 必須宣告 `network` 且使用者授權。
- `health` 以 `/` 開頭；啟動後 15 秒內沒回應視為失敗。

## 其他

`requires`（能力名 `namespace.method`，如 `storage.get`）、`dependsOn`（其他擴充 id）、`expose`（給別的擴充呼叫的方法名）、
`actions`、`config`（作者預設值）、`disabled: { "platform": "macos" }`（在 macOS 停用）——語意同 x-hub。
