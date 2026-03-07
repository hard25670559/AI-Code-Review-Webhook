# AI Code Review Webhook Service 開發規劃文件

> 最後更新：2026-03-07
> 狀態：待開發

## 概述

在自架 GitLab 上，透過 System Hook 監聽所有專案的 MR 事件，自動呼叫 Claude AI 對整個 MR diff 進行 code review，並將建議留言於 MR 中。無需對各專案進行個別設定，全域自動生效。

---

## 架構

```
GitLab MR 事件
     |
     v
GitLab System Hook
     |
     v
Webhook Server (FastAPI)
     |
     +---> Redis（SHA 去重）
     |
     +---> GitLab API（取得 MR 資訊：target/source branch）
     |
     +---> Local Repo（/data/repos/{project_id}/）
     |       |
     |       +---> git clone（首次）/ git fetch（後續）
     |       +---> git diff <target>...<source>（取 MR diff）
     |       +---> 讀檔 / 列目錄 / grep（Tool Use）
     |
     +---> Claude API（Tool Use）
     |
     v
GitLab MR 留言
```

---

## 功能 1：Webhook Server

### 需求描述
- 接收來自 GitLab System Hook 的 MR 事件
- 驗證 Webhook Secret Token
- 非同步處理，避免 GitLab 等待逾時

### 技術規格
- 框架：FastAPI
- 端點：`POST /webhook`
- 非同步處理：使用 `BackgroundTasks`

### 觸發條件
- `object_kind == "merge_request"`
- `action` 為以下之一：`open`、`reopen`、`update`

### 實作細項
- [ ] FastAPI 應用程式建立
- [ ] Webhook Secret Token 驗證（`X-Gitlab-Token` header）
- [ ] MR 事件過濾（object_kind / action）
- [ ] 建立 asyncio.Task 並登記至 Task Registry（見功能 1.5）

### 注意事項
- GitLab System Hook 有 timeout 限制，必須立即回應 200，實際處理在背景執行

---

## 功能 1.5：Task 管理（連續 Push 保護）

### 需求描述
- 同一個 MR 在短時間內連續 push 時，取消舊的 review task，只執行最新的
- 確保 MR 上不會出現多則過時的 AI review 留言
- 與功能 2（SHA 去重）各司其職：本功能處理「不同 SHA 的並發」，功能二處理「相同 SHA 的 retry」

### 技術規格
- per-MR Task Registry：`running_tasks: dict[(project_id, mr_iid), asyncio.Task]`
- 新事件觸發時：cancel 舊 task（若存在）→ 建立新 task 並登記
- 發留言前雙重確認：確認自己仍是 current task，否則靜默放棄

### 流程

```
新 webhook 來（sha_X，與 Redis 不同）
  → 查 running_tasks[(project_id, mr_iid)]
  → 有舊 task → task.cancel()（協作式中斷，在下一個 await 點生效）
  → 建立新 task，登記到 running_tasks
  → 新 task 執行 review
  → 發留言前：確認 running_tasks[(project_id, mr_iid)] 仍是自己
      → 是 → 發留言 → 寫入 Redis
      → 否 → 靜默放棄（被更新的 task 取代了）
```

### 注意事項
- `task.cancel()` 為協作式取消，在 `await` 點拋出 `CancelledError`，不保證即時中斷
- 雙重確認（發留言前）作為補充保障，防止 cancel 時機過晚仍送出舊留言
- Task Registry 存於記憶體（process 重啟後清空，屬正常行為）

### 實作細項
- [ ] `running_tasks` dict 維護（全域或 app state）
- [ ] 新 webhook 觸發時 cancel 舊 task
- [ ] 發留言前確認 current task 身份
- [ ] `CancelledError` 靜默處理（不 log 為 error）

---

## 功能 2：commit SHA 去重

### 需求描述
- 同一個 commit 不重複觸發 review，避免 webhook retry 造成重複留言
- 每個 MR 以最後一次處理的 commit SHA 作為去重依據

### 技術規格
- 儲存：Redis
- Key 格式：`ai_review:{project_id}:{mr_iid}`
- Value：最後一次處理的 commit SHA
- SHA 來源：webhook payload 的 `object_attributes.last_commit.id`（不需額外呼叫 API）

### 實作細項
- [ ] Redis 連線設定
- [ ] 處理前讀取 Redis 比對 SHA
- [ ] 處理完成後寫入最新 SHA

---

## 功能 3：MR 資訊與變動檔案列表取得

### 需求描述
- 從 webhook payload 取得 MR 基本資訊（標題、描述、branch 等），作為 review 的背景脈絡
- 每次 review 只取「有改動的檔案列表」作為初始輸入，不一次送出全部 diff
- Claude 透過 Tool Use 按需索取各檔案的 diff 內容，避免 token 浪費
- 不設截斷上限，由 Claude 自行決定查看哪些檔案

### 技術規格
- MR 資訊來源：webhook payload 的 `object_attributes`
  - 標題：`title`
  - 描述：`description`
  - Source branch：`source_branch`
  - Target branch：`target_branch`
  - 作者：`last_commit.author.name`
- 變動檔案列表指令：`git diff --name-only <target_branch>...<source_branch>`（在 local repo 執行）
- 單一檔案 diff：由 `get_file_diff` Tool 提供（見功能四）

### 實作細項
- [ ] 從 webhook payload 取得 MR 標題、描述、作者
- [ ] 從 webhook payload 取得 target branch 與 source branch
- [ ] 確保 local repo 已更新（透過功能 3.5的 Repo 管理機制）
- [ ] 執行 `git diff --name-only <target_branch>...<source_branch>` 取得變動檔案列表
- [ ] 將 MR 基本資訊 + 檔案列表組合為初始 prompt 送給 Claude

---

## 功能 3.5：Local Repo 管理機制

### 需求描述
- 維護每個專案的 local git repo，避免重複 clone
- 每次 MR 事件觸發時先 fetch，確保 local repo 有最新 commit
- 同一專案並發觸發時，以 asyncio Lock 保護 fetch 操作

### 技術規格
- 存放路徑：`{REPO_BASE_PATH}/{project_id}/`（環境變數 `REPO_BASE_PATH`，預設 `/data/repos`）
- 首次：`git clone <gitlab_repo_url> {path}`
- 後續：`git fetch --all`
- Clone URL 格式：`https://<token>@<gitlab_host>/<namespace>/<project>.git`（帶 token 認證）
- 並發保護：per-project `asyncio.Lock`，只鎖 `git fetch`/`git clone` 步驟

### Repo 初始化流程
```
檢查 {REPO_BASE_PATH}/{project_id}/ 是否存在
  └── 不存在 → git clone
  └── 存在   → git fetch --all
```

### 實作細項
- [ ] `REPO_BASE_PATH` 環境變數讀取（預設 `/data/repos`）
- [ ] per-project asyncio Lock dict 管理
- [ ] `ensure_repo(project_id, repo_url)` 函式：有則 fetch，無則 clone
- [ ] git 指令加上 `GIT_SSL_NO_VERIFY=true`（自簽憑證環境）

---

## 功能 4：Claude AI Review（含 Tool Use）

### 需求描述
- 使用 Claude API 對 MR diff 進行 code review
- Claude 可主動呼叫工具查閱專案內容，做出更準確的判斷
- 透過 Agentic Loop 支援多輪工具呼叫，直到 Claude 完成 review

### 技術規格
- 模型：`claude-sonnet-4-6`
- max_tokens：4096
- 使用 Tool Use 功能

### 提供給 Claude 的工具

#### `get_file_diff`
- 描述：取得該 MR 中某個特定檔案的 diff 內容
- 用途：查看該檔案在此 MR 中的完整變更
- 參數：`file_path`（字串）
- 實作：`git diff <target_branch>...<source_branch> -- <file_path>`

#### `get_file_content`
- 描述：取得專案中某個檔案的完整內容（現有版本）
- 用途：查看相關實作、interface、base class 等完整上下文
- 參數：`file_path`（字串）
- 實作：直接讀取 `{REPO_BASE_PATH}/{project_id}/{file_path}` 檔案內容

#### `list_directory`
- 描述：列出專案中某個目錄的結構
- 參數：`path`（字串，根目錄用空字串）
- 實作：`os.listdir` / `os.scandir` 列出 `{REPO_BASE_PATH}/{project_id}/{path}` 目錄內容

#### `search_in_repo`
- 描述：在專案中搜尋關鍵字，找出相關函數定義、類別、常數等
- 參數：`keyword`（字串）
- 實作：`grep -r --include="*" -l "{keyword}" {REPO_BASE_PATH}/{project_id}/`
- 回傳：最多 5 筆結果（含檔案路徑與匹配行）

#### `get_issue`
- 描述：取得 GitLab Issue 的詳細內容（標題、描述、狀態、標籤等）
- 用途：查看 Issue 需求說明，作為判斷 MR 是否符合需求的標準
- 參數：`issue_iid`（整數，Issue 在該專案中的編號）
- 實作：呼叫 `GET /api/v4/projects/:id/issues/:issue_iid`
- 回傳：Issue 標題、描述、狀態、標籤（限制 3000 字元）

#### `get_issue_notes`
- 描述：取得 GitLab Issue 底下的留言列表
- 用途：查看 Issue 討論內容，補充需求細節或決策背景
- 參數：`issue_iid`（整數，Issue 在該專案中的編號）
- 實作：呼叫 `GET /api/v4/projects/:id/issues/:issue_iid/notes`
- 回傳：最新留言（依時間排序，限制 3000 字元）

### Agentic Loop 流程
1. 送出「MR 基本資訊（標題、描述、作者、branch）+ 變動檔案列表」給 Claude，作為 review 起點
2. Claude 主動呼叫 `get_file_diff` 查看想看的檔案 diff
3. 必要時呼叫 `get_file_content` / `list_directory` / `search_in_repo` 補充上下文
4. 必要時呼叫 `get_issue` 查看對應 Issue 需求，確認 MR 是否符合需求
5. 重複直到 `stop_reason == "end_turn"`
6. 取最終文字回應作為 review 內容

### 實作細項
- [ ] Claude API 呼叫（含 tools 定義）
- [ ] Agentic Loop 實作
- [ ] `get_file_content` 工具實作
- [ ] `list_directory` 工具實作
- [ ] `search_in_repo` 工具實作
- [ ] `get_issue` 工具實作
- [ ] `get_issue_notes` 工具實作
- [ ] 每個工具回傳結果限制 3000 字元

---

## 功能 4.5：AI Provider 切換機制

### 需求描述
- 支援多個 AI 廠商（Anthropic、OpenAI），可透過環境變數切換
- 切換廠商或模型不需修改程式碼，只需改 `.env` 重啟
- 每個 Provider 的 Agentic Loop 實作細節各自封裝，主流程不感知差異

### 技術規格

#### 介面定義（`app/providers/base.py`）
```python
from abc import ABC, abstractmethod
from app.mr_info import MRContext

class BaseReviewer(ABC):
    @abstractmethod
    def run_review(self, ctx: MRContext) -> str: ...
```

#### Provider 實作
| 類別 | 檔案 | 對應廠商 |
|------|------|---------|
| `AnthropicReviewer` | `app/providers/anthropic.py` | Anthropic Claude |
| `OpenAIReviewer` | `app/providers/openai.py` | OpenAI GPT |
| `ClaudeCliReviewer` | `app/providers/claude_cli.py` | Claude CLI（本機） |

每個實作類別負責：
- 初始化對應的 SDK client
- 定義自己格式的 Tool Schemas（Anthropic / OpenAI 格式不同）
- 實作完整的 Agentic Loop（含工具呼叫與結果回饋）

#### Factory（`app/ai_review.py`）
```python
def get_reviewer() -> BaseReviewer: ...  # 依 config.AI_PROVIDER 回傳對應實作
def run_review(ctx: MRContext) -> str: ...  # 委派給 get_reviewer()
```

### 環境變數

| 變數名稱 | 說明 | 可選值 | 預設值 |
|----------|------|--------|--------|
| `AI_PROVIDER` | 使用的 AI 廠商 | `anthropic`、`openai`、`claude_cli` | `anthropic` |
| `AI_MODEL` | 使用的模型名稱 | 各廠商模型 ID | `claude-sonnet-4-6` |
| `ANTHROPIC_API_KEY` | Anthropic API Key | - | （選填，依 provider） |
| `OPENAI_API_KEY` | OpenAI API Key | - | （選填，依 provider） |

> 啟動時若 `AI_PROVIDER` 對應的 API Key 未設定，留言提示並等待（現有行為延伸）

### Tool Schema 格式差異

| 欄位 | Anthropic | OpenAI |
|------|-----------|--------|
| 頂層結構 | `{name, description, input_schema}` | `{type: "function", function: {name, description, parameters}}` |
| 參數欄位名稱 | `input_schema` | `parameters` |
| tool result 格式 | `{type: "tool_result", tool_use_id, content}` | `{role: "tool", tool_call_id, content}` |
| stop 條件 | `stop_reason == "end_turn"` | `finish_reason == "stop"` |
| 工具呼叫條件 | `stop_reason == "tool_use"` | `finish_reason == "tool_calls"` |

各 Provider 自行維護自己格式的 Tool Schemas，不共用。

### 檔案結構異動

```
app/
├── providers/
│   ├── __init__.py
│   ├── base.py          # BaseReviewer 抽象介面
│   ├── anthropic.py     # AnthropicReviewer（原 ai_review.py 的實作搬移）
│   └── openai.py        # OpenAIReviewer
└── ai_review.py         # 僅保留 factory + run_review() 委派
```

### 實作細項
- [ ] `app/providers/base.py`：定義 `BaseReviewer` 抽象類別
- [ ] `app/providers/anthropic.py`：`AnthropicReviewer` 實作（含 Anthropic 格式 Tool Schemas + Agentic Loop）
- [ ] `app/providers/openai.py`：`OpenAIReviewer` 實作（含 OpenAI 格式 Tool Schemas + Agentic Loop）
- [ ] `app/ai_review.py`：改為 factory，依 `AI_PROVIDER` 回傳對應 reviewer
- [ ] `app/config.py`：新增 `AI_PROVIDER`、`AI_MODEL`、`OPENAI_API_KEY`，`ANTHROPIC_API_KEY` 改為 optional
- [ ] `app/task_manager.py`：API key 檢查依 `AI_PROVIDER` 決定檢查哪個 key
- [ ] `requirements.txt`：新增 `openai`

---

## 功能 4.6：差異化 MR Review

### 需求描述
- 若同一個 MR 已有過 AI review，後續新 push 只針對「上次 review 的 SHA → 最新 SHA」之間的差異做 review
- 節省 token，避免重複 review 已審過的內容
- 若沒有過 review 記錄，維持現有完整 review 行為

### 判斷邏輯

```
觸發 review 時：
  1. 從 Redis 取得 last_reviewed_sha
     └── 無記錄 → 執行完整 review（現有行為）
  2. 從 GitLab MR comments 確認是否有 AI review 留言
     └── 無留言（被刪除）→ 退回完整 review
  3. 兩者皆有 → 執行差異 review
```

### 差異 review 流程
1. 帶入差異模式的 prompt（告知 AI 這是補充 review，提供 last SHA 與 current SHA）
2. AI 使用 `get_diff_between_shas` 工具取得差異 diff
3. AI 可選擇性呼叫 `get_previous_review` 工具取得舊 review 內容作為參考
4. AI 完成後以相同格式留言（`## AI Code Review（{sha[:7]}）`）

### 技術規格

#### 識別 AI review 留言
- Pattern：留言內容以 `## AI Code Review（` 開頭
- 來源：GitLab API `GET /api/v4/projects/:id/merge_requests/:mr_iid/notes`

#### 新增工具

##### `get_diff_between_shas`
- 描述：取得兩個 SHA 之間的 diff，用於查看自上次 review 後新增的變更
- 參數：`from_sha`（字串）、`to_sha`（字串）
- 實作：`git diff {from_sha}...{to_sha}`（在 local repo 執行）

##### `get_previous_review`
- 描述：取得此 MR 上一次 AI review 的留言內容，作為理解新 diff 的背景參考
- 參數：無
- 實作：呼叫 GitLab API 取得 MR notes，篩選最新一筆 `## AI Code Review（` 開頭的留言
- 回傳：最新一筆 AI review 留言全文（限制 3000 字元）
- 注意：由 AI 自行決定是否呼叫，不強制

### 邊界情況
- Redis 有 SHA 但 GitLab 找不到 AI review 留言（被刪除）→ 退回完整 review
- from_sha 與 to_sha 相同（無新差異）→ 不觸發 review（SHA 去重機制已處理）

### 實作細項
- [ ] `gitlab_client.py`：新增 `get_mr_notes(project_id, mr_iid)` 函式
- [ ] `tools.py`：新增 `get_diff_between_shas(ctx, from_sha, to_sha)` 工具函式
- [ ] `tools.py`：新增 `get_previous_review(ctx)` 工具函式
- [ ] `app/providers/anthropic.py`：新增兩個工具的 Schema（Anthropic 格式）
- [ ] `app/providers/openai.py`：新增兩個工具的 Schema（OpenAI 格式）
- [ ] `ai_review.py` 或 `task_manager.py`：新增判斷邏輯，依有無舊 review 決定 prompt 模式
- [ ] 差異模式 prompt：告知 AI 這是針對新增差異的補充 review，提供 last SHA 與 current SHA

---

## 功能 4.7：Claude CLI Provider

### 需求描述
- 新增第三個 AI Provider，使用本機安裝的 `claude` CLI（Claude Code）執行 review
- 透過 Redis 儲存 `session_id`，同一 MR 的多次 review 共用同一個 Claude 對話 session，保持跨次呼叫的上下文一致性
- MR merge 時清除 session_id，避免殘留舊 session

### 技術規格

#### ClaudeCliReviewer（`app/providers/claude_cli.py`）

呼叫方式：
```bash
claude -p "<prompt>" \
  --output-format json \
  --mcp-config '<mcp_config_json>' \
  --dangerously-skip-permissions \
  [--resume <session_id>]
```

- **Prompt**：與 Anthropic/OpenAI provider 相同邏輯（MR 基本資訊 + 變動檔案列表），AI 自行決定呼叫哪些工具
- **工具**：透過 MCP server 提供（stdio 模式），工具集與現有 provider 相同
- **Session 延續**：若 Redis 有此 MR 的 `session_id`，加上 `--resume <session_id>` 參數
- **輸出解析**：解析 JSON stdout，`result` 欄位為 review 文字，`session_id` 欄位存回 Redis
- **實作位置**：`ctx.cli_session_id` 欄位（MRContext 新增）用於傳遞 session_id 進出 reviewer

#### MCP Server（`app/mcp_server.py`）

- 使用 `fastmcp` 建立 stdio MCP server
- 暴露以下工具（與現有 `tools.py` 共用實作）：
  - `get_file_diff`、`get_file_content`、`list_directory`、`search_in_repo`
  - `get_issue`、`get_issue_notes`
  - `get_diff_between_shas`、`get_previous_review`
- MRContext 資訊（`project_id`、`mr_iid`、`sha`、`target_branch`、`source_branch`）透過環境變數傳入
- 由 Claude CLI 在需要工具時自動 spawn（stdio transport），每次 review 結束後自動結束

MCP config 結構（傳給 `--mcp-config`）：
```json
{
  "mcpServers": {
    "ai-review": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "MR_PROJECT_ID": "123",
        "MR_IID": "45",
        "MR_SHA": "abc1234...",
        "MR_TARGET_BRANCH": "main",
        "MR_SOURCE_BRANCH": "feature/xxx",
        "MR_LAST_REVIEWED_SHA": ""
      }
    }
  }
}
```

#### Session 管理

| 項目 | 說明 |
|------|------|
| Redis Key | `ai_review:session:{project_id}:{mr_iid}` |
| Value | Claude CLI 回傳的 `session_id`（UUID 字串） |
| TTL | 永不過期 |
| 寫入時機 | 每次 review 完成後，從 `ctx.cli_session_id` 存入 Redis |
| 讀取時機 | 每次 review 開始前，從 Redis 取出填入 `ctx.cli_session_id` |
| 清除時機 | MR merge 事件觸發時 |

#### MR Merge 處理

- webhook 收到 `action == "merge"` 事件時：
  1. 刪除 Redis 中的 `ai_review:session:{project_id}:{mr_iid}`
  2. 不觸發 review，直接回應 `{"status": "ok"}`
- webhook.py 的 `_ALLOWED_ACTIONS` 不包含 `merge`，需另外處理

### 環境變數

| 變數名稱 | 說明 |
|----------|------|
| `AI_PROVIDER=claude_cli` | 指定使用 Claude CLI provider |
| `ANTHROPIC_API_KEY` | Claude CLI 認證使用（與 Anthropic provider 共用） |

### 容器化

- `Dockerfile` 新增 Node.js 安裝與 `npm install -g @anthropic-ai/claude-code`
- `requirements.txt` 新增 `fastmcp`

### 檔案結構異動

```
app/
├── providers/
│   └── claude_cli.py     # ClaudeCliReviewer
└── mcp_server.py          # FastMCP stdio server（暴露工具給 Claude CLI）
```

### 實作細項
- [ ] `app/mr_info.py`：`MRContext` 新增 `cli_session_id: str | None = None` 欄位
- [ ] `app/redis_client.py`：新增 `get_session_id`、`set_session_id`、`delete_session_id` 函式
- [ ] `app/mcp_server.py`：建立 FastMCP stdio server，透過環境變數接收 MRContext，暴露所有工具
- [ ] `app/providers/claude_cli.py`：`ClaudeCliReviewer` 實作（含 MCP config 組裝、subprocess 呼叫、JSON 解析、session_id 更新）
- [ ] `app/ai_review.py`：factory 新增 `claude_cli` case
- [ ] `app/task_manager.py`：review 前讀取 `session_id` 填入 ctx；review 後將 `ctx.cli_session_id` 存回 Redis（僅 `claude_cli` provider）
- [ ] `app/webhook.py`：新增 `action == "merge"` 處理，清除 `cli_session_id`
- [ ] `Dockerfile`：安裝 Node.js + `npm install -g @anthropic-ai/claude-code`
- [ ] `requirements.txt`：新增 `fastmcp`
- [ ] `.env.example`：新增 `AI_PROVIDER=claude_cli` 範例說明

---

## 功能 5：MR 留言

### 需求描述
- 將 AI review 結果以留言形式發佈到對應的 MR
- 留言標題帶上 commit SHA 短碼，方便對應版本

### 技術規格
- API：`POST /api/v4/projects/:id/merge_requests/:mr_iid/notes`
- 留言格式：`## AI Code Review（{sha[:7]}）\n\n{review_text}`

### 實作細項
- [ ] 呼叫 GitLab API 發佈 MR 留言
- [ ] 留言格式套用（含 SHA 短碼）

---

## 部署

### 技術規格
- 容器化：Docker + docker-compose
- 服務：Webhook Server（FastAPI）+ Redis

### 環境變數

| 變數名稱 | 說明 | 範例 |
|----------|------|------|
| `GITLAB_URL` | 自架 GitLab 網址 | `https://gitlab.yourcompany.com` |
| `GITLAB_TOKEN` | Personal Access Token | - |
| `ANTHROPIC_API_KEY` | Claude API Key | - |
| `WEBHOOK_SECRET` | Webhook 驗證 Token | - |
| `REDIS_HOST` | Redis 主機位址 | `redis`（docker-compose 內） |
| `REPO_BASE_PATH` | Local repo 存放根目錄 | `/data/repos` |

### GitLab Token 所需權限
- `api`（用於發佈 MR 留言，GitLab 無更細粒度的 scope）
- `read_repository`（用於 clone 專案）

> 注意：Token 雖然持有 `api` scope，但程式碼層面嚴格限制呼叫的端點範圍（見操作範圍限制章節）

### 實作細項
- [ ] `Dockerfile` 建立（需包含 git）
- [ ] `docker-compose.yml`（Webhook Server + Redis），掛載 `/data/repos` volume
- [ ] 環境變數 `.env.example`

### 自簽憑證處理
- [ ] requests 呼叫加上 `verify=False`（自簽憑證環境）
- [ ] git 操作加上 `http.sslVerify false`（如需要）

---

## 操作範圍限制

### GitLab API 允許的端點

| 用途 | Method | 端點 |
|------|--------|------|
| 取得 MR 資訊 | `GET` | `/api/v4/projects/:id/merge_requests/:mr_iid` |
| 發佈 MR 留言 | `POST` | `/api/v4/projects/:id/merge_requests/:mr_iid/notes` |
| 取得 Issue 內容 | `GET` | `/api/v4/projects/:id/issues/:issue_iid` |
| 取得 Issue 留言 | `GET` | `/api/v4/projects/:id/issues/:issue_iid/notes` |
| 取得 MR 留言 | `GET` | `/api/v4/projects/:id/merge_requests/:mr_iid/notes` |

**禁止呼叫的操作（程式碼層面不實作）：**
- 任何 `PUT` / `PATCH` / `DELETE` 端點
- Merge、Close、Approve MR 等操作
- 其他專案、Branch、Tag、User 等資源的 API

---

### Git 操作允許範圍

**允許（唯讀）：**
- `git clone`（首次建立 local repo）
- `git fetch --all`（更新 local repo）
- `git diff`（查看 diff）
- `git diff --name-only`（查看變動檔案列表）
- 直接讀取 local repo 檔案內容
- `grep`（搜尋檔案內容）
- `os.listdir` / `os.scandir`（列目錄）

**禁止（破壞性操作，不實作）：**
- `git push`
- `git commit`
- `git add` / `git rm`
- `git reset` / `git rebase` / `git merge`
- `git checkout`（切換 / 修改工作目錄）
- 任何修改 remote 的操作

---

## GitLab 設定

### System Hook 設定
- 路徑：Admin Area → System Hooks → Add new hook
- URL：`http://{server_host}:8000/webhook`
- Secret Token：對應 `WEBHOOK_SECRET`
- 勾選事件：`Merge request events`

---

## 待確認事項
- [ ] Webhook Server 部署位置（內網 IP / hostname）
- [ ] GitLab 是否使用自簽憑證
- [ ] Redis 是否需要持久化設定

## 變更記錄
| 日期 | 變更內容 |
|------|----------|
| 2026-03-07 | 初始版本 |
| 2026-03-07 | MR Diff 取得改為 git clone + git diff；新增 Repo 管理機制（功能 3.5）；Tool Use 三個工具改為 local clone 讀取；新增環境變數 `REPO_BASE_PATH` |
| 2026-03-07 | 初始輸入改為只送變動檔案列表（`git diff --name-only`）；新增 `get_file_diff` Tool；Claude 按需索取各檔案 diff，移除截斷上限限制 |
| 2026-03-07 | 移除 GitLab API commit 列表呼叫；SHA 去重改用 webhook payload 的 `last_commit.id`；branch 資訊直接從 payload 取得 |
| 2026-03-07 | 新增功能 1.5：Task 管理，處理連續 push 並發問題；cancel 舊 task + 發留言前雙重確認 |
| 2026-03-07 | 功能 4 新增 `get_issue` 工具，允許 Claude 查看 GitLab Issue 內容作為 code review 判斷標準；更新操作範圍限制 |
| 2026-03-07 | 功能 4 新增 `get_issue_notes` 工具，允許 Claude 查看 Issue 留言補充需求細節 |
| 2026-03-07 | 功能 3 初始 prompt 新增 MR 基本資訊（標題、描述、作者）；資訊來源為 webhook payload，不需額外 API 呼叫 |
| 2026-03-07 | 新增功能 4.6：差異化 MR Review；新增 get_diff_between_shas、get_previous_review 工具；舊 review 留言識別機制；邊界情況處理 |
| 2026-03-08 | 新增功能 4.7：Claude CLI Provider；MCP server 暴露工具；Redis session_id 管理；MR merge 清除 session；Dockerfile 新增 Node.js + Claude CLI |
