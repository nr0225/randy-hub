# Randy Hub 內建範例擴充

預設每次啟動都從本目錄載入（不寫死絕對路徑，repo 搬家或合併後照樣可用）；不想看到可在「設定 → 外觀 → 載入內建範例擴充」關掉。

| 目錄 | id | 用途 | `precheck` |
|---|---|---|---|
| `hello/` | `com.randy.hello` | 儀表板卡片 + 完整頁面：storage / config / 主題 / 軍師狀態 / 通知 | **故意不過**：自檢區會呼叫未宣告的 `sharedStorage` / `fs` / `data.*`，用來證明執行期會被擋 |
| `security-probe/` | `com.randy.security-probe` | 不宣告任何權限，主動越權 11 項，全部被擋才算 PASS（改寫自 x-hub `tests/extensions/security-probe`） | **故意不過**（同上） |
| `echo-service/` | `com.randy.echo-service` | Python 背景服務：預設不啟動、信任此版本後才可啟動；前端經 `xhub.service.request`；後端用令牌取回**自己的**機密 | 通過 |

寫真正要用的擴充時，`python3 -m strategist.hub precheck <目錄>` 必須通過（0 error）。
