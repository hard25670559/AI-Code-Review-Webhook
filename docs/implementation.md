# AI Code Review Webhook — 實作規劃文件

> 本文件說明如何依照 `spec.md` 逐步落地實作，包含檔案結構、各檔案職責與分階段實作步驟。

---

## 專案目錄結構

```
ai-review-webhook/
├── app/
│   ├── __init__.py
│   ├── main.py            # FastAPI 應用程式入口，掛載路由
│   ├── config.py          # 環境變數讀取（集中管理）
│   ├── webhook.py         # POST /webhook 端點、事件過濾（功能 1）
│   ├── task_manager.py    # per-MR Task Registry，連續 push 保護（功能 1.5）
│   ├── redis_client.py    # Redis 連線與 SHA 去重操作（功能 2）
│   ├── mr_info.py         # 從 payload 組合 MR 資訊 + git diff 檔案列表（功能 3）
│   ├── repo_manager.py    # git clone / fetch + asyncio Lock（功能 3.5）
│   ├── ai_review.py       # Claude API Agentic Loop（功能 4）
│   ├── tools.py           # 所有 Tool Use 工具實作（功能 4）
│   └── gitlab_client.py   # GitLab API：取 MR/Issue 資訊、發留言（功能 5）
├── Dockerfile
├── docker-compose.yml
├── .env.example
└── requirements.txt
```

---

## 各檔案職責說明

| 檔案 | 職責 |
|------|------|
| `config.py` | 讀取所有環境變數，提供全域 settings 物件 |
| `webhook.py` | 驗證 X-Gitlab-Token、過濾 MR 事件、呼叫 task_manager |
| `task_manager.py` | 維護 `running_tasks` dict，cancel 舊 task、建立新 task |
| `redis_client.py` | 連線 Redis、讀取/寫入 SHA 去重 key |
| `mr_info.py` | 從 webhook payload 萃取 MR metadata，呼叫 repo_manager 後取得檔案列表 |
| `repo_manager.py` | `ensure_repo()`：有則 fetch，無則 clone；per-project asyncio Lock |
| `ai_review.py` | 組合初始 prompt、執行 Agentic Loop、回傳最終 review 文字 |
| `tools.py` | 實作 6 個 Tool Use 工具函式 |
| `gitlab_client.py` | 封裝允許範圍內的 GitLab API 呼叫（GET MR、GET Issue、POST 留言） |
| `main.py` | 建立 FastAPI app、掛載 router、初始化 Redis/settings |

---

## 實作步驟

### Step 1：環境建置

建立以下基礎設施檔案：

**`requirements.txt`**
```
fastapi
uvicorn[standard]
redis[asyncio]
anthropic
requests
python-dotenv
```

**`Dockerfile`**
- 基底映像：`python:3.12-slim`
- 安裝 git（repo 操作必要）
- 複製 requirements.txt，執行 `pip install`
- 複製 `app/` 目錄
- 啟動指令：`uvicorn app.main:app --host 0.0.0.0 --port 8000`

**`docker-compose.yml`**
- 服務一：`webhook`（FastAPI），port 8000，掛載 `/data/repos` volume，讀取 `.env`
- 服務二：`redis`，使用官方 redis 映像

**`.env.example`**
```
GITLAB_URL=https://gitlab.yourcompany.com
GITLAB_TOKEN=
ANTHROPIC_API_KEY=
WEBHOOK_SECRET=
REDIS_HOST=redis
REPO_BASE_PATH=/data/repos
```

---

### Step 2：config.py — 環境變數集中管理

- 使用 `python-dotenv` 載入 `.env`
- 定義 `Settings` dataclass 或直接用模組層級變數：
  - `GITLAB_URL`、`GITLAB_TOKEN`
  - `ANTHROPIC_API_KEY`
  - `WEBHOOK_SECRET`
  - `REDIS_HOST`（預設 `redis`）
  - `REPO_BASE_PATH`（預設 `/data/repos`）

---

### Step 3：redis_client.py — SHA 去重（功能 2）

- 使用 `redis.asyncio` 建立非同步連線
- Key 格式：`ai_review:{project_id}:{mr_iid}`
- 提供兩個函式：
  - `get_processed_sha(project_id, mr_iid) -> str | None`：讀取已處理的 SHA
  - `set_processed_sha(project_id, mr_iid, sha)`：寫入最新 SHA

---

### Step 4：gitlab_client.py — GitLab API 封裝（功能 5 + Issue 工具）

- 所有呼叫使用 `requests`，帶上 `Authorization: Bearer {GITLAB_TOKEN}` header
- 自簽憑證環境：`verify=False`
- 嚴格限制只實作以下方法：
  - `get_mr(project_id, mr_iid) -> dict`
  - `post_mr_comment(project_id, mr_iid, body: str)`
  - `get_issue(project_id, issue_iid) -> dict`
  - `get_issue_notes(project_id, issue_iid) -> list`
- **不實作** 任何 PUT / PATCH / DELETE 操作

---

### Step 5：repo_manager.py — Local Repo 管理（功能 3.5）

- 全域 `repo_locks: dict[int, asyncio.Lock]`，per-project Lock
- 核心函式 `ensure_repo(project_id, repo_url)`：
  1. 取得（或建立）該 project 的 asyncio Lock
  2. `async with lock:`
  3. 若 `{REPO_BASE_PATH}/{project_id}/` 不存在 → `git clone <url> <path>`
  4. 若已存在 → `git fetch --all`
  5. git 指令加上環境變數 `GIT_SSL_NO_VERIFY=1`
- Clone URL 格式：`https://{GITLAB_TOKEN}@{gitlab_host}/{namespace}/{project}.git`
  - namespace/project 從 `gitlab_client.get_mr()` 的 `path_with_namespace` 取得

---

### Step 6：mr_info.py — MR 資訊與檔案列表（功能 3）

- 從 webhook payload 的 `object_attributes` 萃取：
  - `project_id`、`mr_iid`
  - `title`、`description`、`source_branch`、`target_branch`
  - `last_commit.id`（SHA）、`last_commit.author.name`（作者）
- 呼叫 `ensure_repo()` 確保 local repo 已更新
- 執行 `git diff --name-only {target_branch}...{source_branch}` 取得變動檔案列表
- 回傳組合好的 MR context dict，供後續步驟使用

---

### Step 7：tools.py — Tool Use 工具實作（功能 4）

每個工具函式接收 `project_id`、相關參數，回傳字串結果（限制 3000 字元）。

**`get_file_diff(project_id, file_path, target_branch, source_branch) -> str`**
- 執行：`git diff {target_branch}...{source_branch} -- {file_path}`
- 於 `{REPO_BASE_PATH}/{project_id}/` 執行

**`get_file_content(project_id, file_path) -> str`**
- 直接讀取 `{REPO_BASE_PATH}/{project_id}/{file_path}` 檔案內容

**`list_directory(project_id, path) -> str`**
- 使用 `os.scandir({REPO_BASE_PATH}/{project_id}/{path})` 列出目錄內容
- 區分檔案與目錄，回傳格式化列表

**`search_in_repo(project_id, keyword) -> str`**
- 執行：`grep -r -n "{keyword}" {REPO_BASE_PATH}/{project_id}/`
- 回傳最多 5 筆結果（含檔案路徑與匹配行號）

**`get_issue(project_id, issue_iid) -> str`**
- 呼叫 `gitlab_client.get_issue()`
- 回傳：標題、描述、狀態、標籤的格式化文字

**`get_issue_notes(project_id, issue_iid) -> str`**
- 呼叫 `gitlab_client.get_issue_notes()`
- 依時間排序，回傳留言列表（作者 + 內容）

**工具 schema 定義**（傳給 Claude API 的 `tools` 參數）
- 為每個工具定義 JSON Schema，包含 name、description、input_schema

---

### Step 8：ai_review.py — Claude Agentic Loop（功能 4）

- 接收 MR context（標題、描述、作者、branch、變動檔案列表）
- 組合初始 user message：
  ```
  以下是一個 GitLab MR 的資訊，請進行 code review：

  標題：{title}
  作者：{author}
  描述：{description}
  Target Branch：{target_branch}
  Source Branch：{source_branch}

  變動檔案列表：
  {file_list}

  請主動使用工具查看需要的檔案 diff 與內容，完成後提供 code review 建議。
  ```
- Agentic Loop：
  1. 呼叫 Claude API（`claude-sonnet-4-6`，max_tokens=4096，帶 tools 定義）
  2. 若 `stop_reason == "tool_use"`：
     - 解析 `tool_use` block，呼叫對應工具函式
     - 將工具結果以 `tool_result` 格式加入 messages
     - 重複步驟 1
  3. 若 `stop_reason == "end_turn"`：
     - 取出最後的文字回應，回傳
- 工具 dispatch：根據 tool name 呼叫 `tools.py` 中對應函式

---

### Step 9：task_manager.py — Task Registry（功能 1.5）

- 全域 `running_tasks: dict[tuple[int, int], asyncio.Task]`（key = `(project_id, mr_iid)`）
- 函式 `submit_review_task(project_id, mr_iid, coro)`：
  1. 若 key 已存在且 task 未完成 → `task.cancel()`
  2. 以 `asyncio.create_task(coro)` 建立新 task
  3. 登記到 `running_tasks`
- Review coroutine 發留言前的雙重確認：
  ```python
  if running_tasks.get((project_id, mr_iid)) is not asyncio.current_task():
      return  # 靜默放棄，已被新 task 取代
  ```
- `CancelledError` 靜默處理（catch 後直接 return，不 log 為 error）

---

### Step 10：webhook.py + main.py — Webhook Server 組裝（功能 1）

**`webhook.py`**
- `POST /webhook` endpoint
- 驗證 `X-Gitlab-Token` header（與 `WEBHOOK_SECRET` 比對）
- 過濾條件：
  - `object_kind == "merge_request"`
  - `action` 在 `["open", "reopen", "update"]`
- SHA 去重：從 payload 取 `last_commit.id`，比對 Redis，相同則跳過
- 符合條件 → 呼叫 `task_manager.submit_review_task()`，立即回應 200

**Review 主流程 coroutine**（在 task 中非同步執行）：
```
1. ensure_repo()（功能 3.5）
2. 取得變動檔案列表（功能 3）
3. ai_review()（功能 4）
4. 雙重確認 current task
5. gitlab_client.post_mr_comment()（功能 5）
6. redis_client.set_processed_sha()（功能 2）
```

**`main.py`**
- 建立 FastAPI app
- 在 `lifespan` 中初始化 Redis 連線
- 掛載 webhook router

---

## 實作順序總結

| 順序 | 檔案 | 對應功能 |
|------|------|---------|
| 1 | `Dockerfile`、`docker-compose.yml`、`requirements.txt`、`.env.example` | 環境建置 |
| 2 | `config.py` | 環境變數 |
| 3 | `redis_client.py` | 功能 2 |
| 4 | `gitlab_client.py` | 功能 5 基礎 |
| 5 | `repo_manager.py` | 功能 3.5 |
| 6 | `mr_info.py` | 功能 3 |
| 7 | `tools.py` | 功能 4 工具 |
| 8 | `ai_review.py` | 功能 4 Agentic Loop |
| 9 | `task_manager.py` | 功能 1.5 |
| 10 | `webhook.py` + `main.py` | 功能 1 |
