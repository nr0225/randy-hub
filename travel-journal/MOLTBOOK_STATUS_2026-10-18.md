# Moltbook 發表狀態｜2026-10-18

專案正本：[帶 AI 看台灣・高雄](./2026-10-11-kaohsiung-night-to-dawn.md)

## 已驗證
- 使用者已認領 Moltbook Agent `randysworldcompanion`。
- 文章已用該 Agent 發送到 `m/general`，標題為「A human took an AI to see his world — Kaohsiung, Taiwan, from midnight to dawn」。
- 實際 Moltbook 文章 ID：`efb5ee0f-91b9-4698-b146-f57b7a962925`。
- 文章連結：https://www.moltbook.com/post/efb5ee0f-91b9-4698-b146-f57b7a962925
- 2026-10-18 公開 API 讀取：`verification_status=pending`，**不算正式發表完成**。
- 文章 `created_at=2026-10-17T18:15:31.276Z`（台灣 2026-10-18 02:15）。
- 第一次 challenge 因超過五分鐘回覆 `HTTP 410 Challenge expired`；舊碼不能重試。
- `--retry-expired` 重新 POST 時回傳同一文章 ID、沒有新題目：疑似平台同文去重；**此方法已停用**。

## 保守修復
- 已準備 `travel-journal/moltbook_publish.py --repair-pending`，先 GET 確認本人、尚未驗證、仍是 pending、帳號 claimed、建立時間已超過兩小時又 60 秒緩衝。
- 顯式輸入 `DELETE_REPOST` 才會刪除同帳號原本 **pending** 的舊文；之後只投稿一次、記錄新 ID，且不會自動重試。
- 冷卻時間依原貼文建立時間計：台灣時間 **04:16:31 之後**；建議 04:17 後執行。
- 預期得到新的 challenge 並於 5 分鐘內作答；若平台未回傳驗證題，應停止寫入、檢查公開狀態，避免重複內容。
- 正式驗收：`GET /api/v1/posts/{id}` 回傳 `verification_status=verified`，且公開 feed 能讀到相同 ID。

## 安全
- 不含註冊 API Key、登入資料、驗證 code、未授權照片、對話原文或住址。
- GitHub 原始旅行文稿與 AI 社群貼文草稿均保留。
- **這份進度紀錄不是發布成功證明。**
