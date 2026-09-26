# 橋 API（`window.xhub`，別名 `window.randyHub`）

> 改寫自 x-hub `references/bridge-api.md`（MIT, © 2026 dckxx）。宿主在入口 HTML 自動注入，**不要 import 任何套件**。
> 所有方法回 Promise；失敗 reject 帶 `code` 的 Error（`PERMISSION_DENIED` / `CAPABILITY_UNAVAILABLE` / `INVALID_ARGUMENT` / `QUOTA_EXCEEDED` / `NOT_TRUSTED` …）。

## 能力表（由 `strategist/hub/capabilities.py` 產生，與程式一致）

| 能力 | 需要權限 | 風險 |
|---|---|---|
| `config.all` / `config.get` / `config.set` / `config.remove` | 免 | — |
| `storage.get` / `storage.set` / `storage.remove` / `storage.clear` | 免 | — |
| `theme.get` | 免 | — |
| `runtime.info` / `runtime.callExtension` / `runtime.openExternal` | 免 | — |
| `service.request` | 免（只限 runtime=service 且已信任、已啟動） | — |
| `events.emit` | `events` | low |
| `notify.show` | `notify` | low |
| `sharedStorage.get` / `set` / `remove` | `shared-storage` | medium |
| `fs.saveText` / `fs.saveFile` | `fs` | medium |
| `strategist.status` / `strategist.providers` | `strategist:read` | medium |
| `strategist.command` | `strategist:command` | **high（預設關閉）** |

宿主端處理、不在能力表：`events.on/off`、`runtime.open(surface)`、`openExternal(url)`（會轉成 `runtime.openExternal`）、`expose(method, fn)`。

## 用法

```js
const info = await xhub.runtime.info()        // { id, version, runtime, serviceReady, capabilities, permissions, platform: 'macos' }
await xhub.storage.set('state', { n: 1 })       // 按擴充隔離；module / view / drawer 共用同一份
const v = await xhub.config.get('greeting')     // 使用者覆蓋 ?? manifest.config 預設
const theme = await xhub.theme.get()            // { mode, tokens, ... }；主題變更時會收到 'theme-changed'
xhub.events.on('theme-changed', (t) => {})
xhub.events.emit('my-event', data)              // 需 events；其他已開啟的擴充會收到
await xhub.notify.show({ title: '完成', body: '已同步' })   // 需 notify；每秒最多 1 則
await xhub.fs.saveText({ name: 'report.md', content })    // 需 fs；存到 ~/Downloads，同名自動加 (1)
const s = await xhub.strategist.status()        // 需 strategist:read；閘道健康、provider 插槽、目前路由（不含任何 key）
await xhub.strategist.command('幫我整理今天待辦', { target: 'nim' })   // 需 strategist:command（高危，使用者要自己開）
const res = await xhub.service.request('/api/x', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}' })
const data = await res.json()                   // res.status / res.headers / res.text()
xhub.openExternal('https://example.com')        // 只放行 http/https，用系統預設瀏覽器開
```

## 不支援（呼叫會回 `CAPABILITY_UNAVAILABLE` 或根本沒有這個方法）

- x-hub 的 `data.*`（筆記 / 待辦 / 便簽 / 速達 / 提示詞）：Randy Hub 沒有這套資料模型。
- `fs.saveAs`、`clipboard.*`、`net.*`、`system.*`、`ui.*`、`fs.readText` 系列。
- 機密讀取：**前端拿不到任何機密**。需要 key 就寫 service 後端，由後端用令牌向 Hub 取「自己的」機密（見 `service.md`）。

判斷能不能用，以 `runtime.info().capabilities` 為準。
