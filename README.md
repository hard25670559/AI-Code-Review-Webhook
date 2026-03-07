# AI Code Review Webhook

在自架 GitLab 上，Code Review 往往依賴人工逐一審查，容易因為時間壓力或遺漏而降低品質。這個專案透過 GitLab System Hook 自動監聽所有 MR 事件，在開發者發出 MR 的同時，由 AI 主動閱讀 diff、查閱相關檔案與 Issue 需求，並將 Code Review 建議直接留言於 MR 中——**不需要對每個專案個別設定，全域自動生效**。

開發者不需要改變任何工作流程，只需要照常發 MR，AI Review 就會自動出現。

---

## 解決的問題

**人工 Review 的限制：**
- Review 速度跟不上開發節奏，MR 在 queue 中等待
- 對熟悉的程式碼容易疏忽、對不熟悉的部分又花太多時間
- 連續 push 修改同一個 MR 時，重複 review 造成干擾

**這個專案如何解決：**
- MR 一送出，AI 立即自動 review，不需等待人工排程
- AI 會主動查閱 diff、讀取相關檔案、搜尋程式碼脈絡，甚至查看對應的 GitLab Issue 需求，理解變更的完整背景再給出建議
- 同一個 MR 後續有新 push 時，只針對「新增的變更」補充 review，不重複審查已看過的內容
- 短時間內連續 push 時，自動取消舊的 review，只對最新版本進行分析

---

## 快速開始

### 1. 設定環境變數

```bash
cp .env.example .env
# 填入 GITLAB_URL、GITLAB_TOKEN、WEBHOOK_SECRET、AI_PROVIDER 等設定
```

### 2. 啟動服務

```bash
docker-compose up -d --build
```

### 3. 設定 GitLab System Hook

進入 **Admin Area → System Hooks → Add new hook**，填入：
- **URL**：`http://{server_host}:8000/webhook`
- **Secret Token**：對應 `.env` 中的 `WEBHOOK_SECRET`
- **勾選事件**：`Merge request events`

完成後，所有專案的 MR 事件會自動觸發 AI Review。

---

## AI Provider

支援三種 AI 廠商，透過 `.env` 中的 `AI_PROVIDER` 切換，不需修改程式碼：

| Provider | 說明 |
|----------|------|
| `anthropic` | Anthropic Claude API（預設） |
| `openai` | OpenAI GPT API |
| `claude_cli` | 本機 Claude CLI，可使用 Claude.ai 訂閱方案 |

使用 `claude_cli` 且透過訂閱方案登入時：
```bash
docker exec -it ai-review-webhook-webhook-1 claude login
```

---

## 深入了解

- [開發規格](docs/spec.md)：功能設計、技術規格、操作範圍限制
- [實作文件](docs/implementation.md)：檔案結構、各模組職責、逐步實作說明
