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

### Step 0：AI Provider 切換機制（功能 4.5）

> 此步驟為既有程式碼的重構，需在 Step 1–10 完成的基礎上進行。

---

#### Step 0-1：建立 providers 目錄結構

新增以下檔案：
```
app/providers/
├── __init__.py      # 空檔案
├── base.py          # BaseReviewer 抽象介面
├── anthropic.py     # AnthropicReviewer 實作
└── openai.py        # OpenAIReviewer 實作
```

---

#### Step 0-2：`app/providers/base.py` — 抽象介面

```python
from abc import ABC, abstractmethod
from app.mr_info import MRContext

class BaseReviewer(ABC):
    @abstractmethod
    def run_review(self, ctx: MRContext) -> str: ...
```

職責：定義所有 Provider 必須實作的介面，確保 `run_review()` 簽名一致。

---

#### Step 0-3：`app/providers/anthropic.py` — AnthropicReviewer

將現有 `ai_review.py` 的實作搬移至此，包含：

- `_TOOL_SCHEMAS`：Anthropic 格式的工具定義（`input_schema` 欄位）
- `_build_initial_prompt(ctx)`：組合初始 prompt（與現有相同）
- `_dispatch_tool(ctx, tool_name, tool_input)`：工具 dispatch（與現有相同）
- `AnthropicReviewer` 類別：
  - `__init__`：初始化 `anthropic.Anthropic(api_key=...)`
  - `run_review(ctx)`：Agentic Loop（`stop_reason == "tool_use"` / `"end_turn"`）

Agentic Loop 細節：
```
messages = [user: 初始 prompt]
loop:
  response = client.messages.create(model, max_tokens, tools, messages)
  messages.append(assistant: response.content)
  if stop_reason == "end_turn" → return text
  if stop_reason == "tool_use":
    for each tool_use block:
      result = _dispatch_tool(...)
      tool_results.append({type: "tool_result", tool_use_id, content})
    messages.append(user: tool_results)
```

---

#### Step 0-4：`app/providers/openai.py` — OpenAIReviewer

- `_TOOL_SCHEMAS`：OpenAI function calling 格式（`type: "function"`, `parameters` 欄位）
- `OpenAIReviewer` 類別：
  - `__init__`：初始化 `openai.OpenAI(api_key=...)`
  - `run_review(ctx)`：Agentic Loop（`finish_reason == "tool_calls"` / `"stop"`）

Agentic Loop 細節（與 Anthropic 的差異）：
```
messages = [user: 初始 prompt]
loop:
  response = client.chat.completions.create(model, max_tokens, tools, messages)
  choice = response.choices[0]
  messages.append(choice.message)          ← 直接 append message object
  if finish_reason == "stop" → return choice.message.content
  if finish_reason == "tool_calls":
    for each tool_call in choice.message.tool_calls:
      tool_input = json.loads(tool_call.function.arguments)
      result = _dispatch_tool(...)
      messages.append({role: "tool", tool_call_id, content: result})
```

Tool Schema 格式對照：
| 欄位 | Anthropic | OpenAI |
|------|-----------|--------|
| 頂層 | `{name, description, input_schema}` | `{type: "function", function: {name, description, parameters}}` |
| 參數 | `input_schema` | `parameters` |
| tool result | `{type: "tool_result", tool_use_id, content}` | `{role: "tool", tool_call_id, content}` |

---

#### Step 0-5：`app/ai_review.py` — 改為 Factory

原有實作移至 `providers/anthropic.py`，此檔案僅保留：

```python
from app import config
from app.providers.base import BaseReviewer
from app.providers.anthropic import AnthropicReviewer
from app.providers.openai import OpenAIReviewer
from app.mr_info import MRContext

def get_reviewer() -> BaseReviewer:
    if config.AI_PROVIDER == "openai":
        return OpenAIReviewer(config.OPENAI_API_KEY, config.AI_MODEL)
    return AnthropicReviewer(config.ANTHROPIC_API_KEY, config.AI_MODEL)

def run_review(ctx: MRContext) -> str:
    return get_reviewer().run_review(ctx)
```

---

#### Step 0-6：`app/config.py` — 新增 Provider 相關設定

新增：
```python
AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")   # "anthropic" | "openai"
AI_MODEL    = os.getenv("AI_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # optional
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")     # optional
```

注意：`ANTHROPIC_API_KEY` 從 `os.environ["..."]`（強制必填）改為 `os.getenv("...", "")`（選填）。

---

#### Step 0-7：`app/task_manager.py` — API Key 檢查依 Provider

現有邏輯：
```python
if not config.ANTHROPIC_API_KEY:
    # 發留言提示
```

改為：
```python
api_key = config.ANTHROPIC_API_KEY if config.AI_PROVIDER == "anthropic" else config.OPENAI_API_KEY
if not api_key:
    # 發留言提示（訊息帶上 provider 名稱）
```

---

#### Step 0-8：`requirements.txt` — 新增 openai

```
fastapi
uvicorn[standard]
redis[asyncio]
anthropic
openai          ← 新增
requests
python-dotenv
```

---

#### Step 0-9：`.env` — 新增環境變數

新增三行：
```
AI_PROVIDER=anthropic
AI_MODEL=claude-sonnet-4-6
OPENAI_API_KEY=
```

---

#### 實作完成後目錄結構

```
app/
├── providers/
│   ├── __init__.py
│   ├── base.py          # BaseReviewer（介面）
│   ├── anthropic.py     # AnthropicReviewer（實作）
│   └── openai.py        # OpenAIReviewer（實作）
├── ai_review.py         # factory only
├── config.py
├── task_manager.py
└── ...（其餘不變）
```

---

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

---

### Step 11：差異化 MR Review（功能 4.6）

> 在 Step 0–10 完成的基礎上進行，屬於新功能擴充。

---

#### Step 11-1：`gitlab_client.py` — 新增 `get_mr_notes()`

```python
def get_mr_notes(project_id: int, mr_iid: int) -> list:
    # GET /api/v4/projects/:id/merge_requests/:mr_iid/notes
    # 回傳 MR 所有留言的列表
```

---

#### Step 11-2：`MRContext` — 新增 `last_reviewed_sha` 欄位

在 `app/mr_info.py` 的 `MRContext` 中新增選填欄位：

```python
@dataclass
class MRContext:
    # ... 現有欄位 ...
    last_reviewed_sha: str | None = None   # 有過 review 時填入，否則為 None
```

此欄位由 `task_manager.py` 在判斷差異 review 後填入。

---

#### Step 11-3：`task_manager.py` — 差異 review 判斷邏輯

在 `_review_task(ctx)` 中，於呼叫 `ai_review.run_review()` 前新增判斷：

```
流程：
1. 從 Redis 取得 last_reviewed_sha（現有 get_processed_sha）
2. 若有 last_reviewed_sha：
   a. 呼叫 gitlab_client.get_mr_notes() 取得 MR 留言列表
   b. 過濾是否有以「## AI Code Review（」開頭的留言
   c. 有 → ctx.last_reviewed_sha = last_reviewed_sha（差異模式）
   d. 無（被刪除）→ 維持 ctx.last_reviewed_sha = None（退回完整 review）
3. 若無 last_reviewed_sha → ctx.last_reviewed_sha = None（完整 review）
```

---

#### Step 11-4：`tools.py` — 新增兩個工具函式

**`get_diff_between_shas(ctx: MRContext, from_sha: str, to_sha: str) -> str`**
- 執行：`git diff {from_sha}...{to_sha}`
- 於 `{REPO_BASE_PATH}/{ctx.project_id}/` 執行
- 回傳限制 3000 字元

**`get_previous_review(ctx: MRContext) -> str`**
- 呼叫 `gitlab_client.get_mr_notes(ctx.project_id, ctx.mr_iid)`
- 過濾出以 `## AI Code Review（` 開頭的留言
- 回傳最新一筆（依 `created_at` 排序），限制 3000 字元
- 若找不到 → 回傳 `（找不到過去的 review 記錄）`

---

#### Step 11-5：各 Provider — 新增工具 Schema 與 dispatch 處理

**`app/providers/anthropic.py`** 的 `_TOOL_SCHEMAS` 新增：

```python
{
    "name": "get_diff_between_shas",
    "description": "取得兩個 commit SHA 之間的 diff，用於查看自上次 review 後新增的變更。",
    "input_schema": {
        "type": "object",
        "properties": {
            "from_sha": {"type": "string", "description": "起始 SHA（上次 review 的 SHA）"},
            "to_sha":   {"type": "string", "description": "結束 SHA（目前最新的 SHA）"},
        },
        "required": ["from_sha", "to_sha"],
    },
},
{
    "name": "get_previous_review",
    "description": "取得此 MR 上一次 AI review 的留言內容，作為理解新 diff 的背景參考。若覺得對理解新變更有幫助才需呼叫。",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
},
```

**`app/providers/openai.py`** 以 OpenAI function calling 格式新增相同兩個工具。

**`app/tools.py`** 的 `dispatch_tool()` 新增對應的 case：
```python
elif tool_name == "get_diff_between_shas":
    return get_diff_between_shas(ctx, tool_input["from_sha"], tool_input["to_sha"])
elif tool_name == "get_previous_review":
    return get_previous_review(ctx)
```

---

#### Step 11-6：各 Provider — 差異模式 Prompt

在 `_build_initial_prompt(ctx)` 中根據 `ctx.last_reviewed_sha` 切換 prompt 結尾：

**完整 review（`last_reviewed_sha is None`）**
```
請主動使用工具查看需要的檔案 diff 與相關內容，完成後提供詳細的 code review 建議。
```

**差異 review（`last_reviewed_sha is not None`）**
```
此 MR 先前已有過 AI review（SHA：{last_reviewed_sha[:7]}）。
本次只需針對 {last_reviewed_sha[:7]} → {sha[:7]} 之間的新增變更進行補充 review。
請使用 get_diff_between_shas 工具取得差異內容。
如有需要，可使用 get_previous_review 工具查看上次的 review 結論作為參考。
完成後提供針對新增變更的 code review 建議。
```

---

### Step 12：Claude CLI Provider（功能 4.7）

> 在 Step 0–11 完成的基礎上進行，屬於新功能擴充。

---

#### Step 12-1：`app/mr_info.py` — MRContext 新增 `cli_session_id` 欄位

在 `MRContext` dataclass 中新增選填欄位：

```python
@dataclass
class MRContext:
    # ... 現有欄位 ...
    last_reviewed_sha: str | None = None
    cli_session_id: str | None = None   # 新增：Claude CLI session，由 task_manager 填入/讀取
```

此欄位用途：
- `task_manager.py` 在 review 前從 Redis 讀取 session_id 填入
- `ClaudeCliReviewer.run_review()` 執行後將新的 session_id 寫回此欄位
- `task_manager.py` 在 review 後從此欄位取出並存回 Redis

---

#### Step 12-2：`app/redis_client.py` — 新增 session_id 操作

新增三個函式，Key 格式為 `ai_review:session:{project_id}:{mr_iid}`：

```python
async def get_session_id(project_id: int, mr_iid: int) -> str | None:
    r = await get_redis()
    return await r.get(f"ai_review:session:{project_id}:{mr_iid}")

async def set_session_id(project_id: int, mr_iid: int, session_id: str) -> None:
    r = await get_redis()
    await r.set(f"ai_review:session:{project_id}:{mr_iid}", session_id)
    # 無 TTL，永不過期

async def delete_session_id(project_id: int, mr_iid: int) -> None:
    r = await get_redis()
    await r.delete(f"ai_review:session:{project_id}:{mr_iid}")
```

---

#### Step 12-3：`app/mcp_server.py` — FastMCP stdio server

建立新檔案 `app/mcp_server.py`，作為 Claude CLI 的工具橋接層。

**Context 讀取（從環境變數）：**
```python
import os
from mcp.server.fastmcp import FastMCP
from app import tools, config
from app.mr_info import MRContext

mcp = FastMCP("ai-review")

# 從環境變數讀取 MRContext 資訊（由 ClaudeCliReviewer 啟動時傳入）
_project_id    = int(os.environ["MR_PROJECT_ID"])
_mr_iid        = int(os.environ["MR_IID"])
_sha           = os.environ["MR_SHA"]
_target_branch = os.environ["MR_TARGET_BRANCH"]
_source_branch = os.environ["MR_SOURCE_BRANCH"]
_last_sha      = os.environ.get("MR_LAST_REVIEWED_SHA") or None
```

**工具定義（對應 `tools.py` 的所有函式）：**
```python
@mcp.tool()
def get_file_diff(file_path: str) -> str:
    return tools.get_file_diff(_project_id, file_path, _target_branch, _source_branch)

@mcp.tool()
def get_file_content(file_path: str) -> str:
    return tools.get_file_content(_project_id, file_path)

@mcp.tool()
def list_directory(path: str) -> str:
    return tools.list_directory(_project_id, path)

@mcp.tool()
def search_in_repo(keyword: str) -> str:
    return tools.search_in_repo(_project_id, keyword)

@mcp.tool()
def get_issue(issue_iid: int) -> str:
    return tools.get_issue(_project_id, issue_iid)

@mcp.tool()
def get_issue_notes(issue_iid: int) -> str:
    return tools.get_issue_notes(_project_id, issue_iid)

@mcp.tool()
def get_diff_between_shas(from_sha: str, to_sha: str) -> str:
    # 需建立臨時 ctx 傳入
    ctx = _make_ctx()
    return tools.get_diff_between_shas(ctx, from_sha, to_sha)

@mcp.tool()
def get_previous_review() -> str:
    ctx = _make_ctx()
    return tools.get_previous_review(ctx)
```

**啟動（stdio transport）：**
```python
if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

#### Step 12-4：`app/providers/claude_cli.py` — ClaudeCliReviewer

建立新檔案，繼承 `BaseReviewer`。

**Prompt 建構（`_build_initial_prompt`）：**
與 `app/providers/anthropic.py` 的 `_build_initial_prompt` 邏輯完全相同（依 `ctx.last_reviewed_sha` 切換完整/差異模式），直接複用或共用。

**MCP config 組裝：**
```python
def _build_mcp_config(ctx: MRContext) -> str:
    import json
    return json.dumps({
        "mcpServers": {
            "ai-review": {
                "command": "python",
                "args": ["-m", "app.mcp_server"],
                "env": {
                    "MR_PROJECT_ID": str(ctx.project_id),
                    "MR_IID": str(ctx.mr_iid),
                    "MR_SHA": ctx.sha,
                    "MR_TARGET_BRANCH": ctx.target_branch,
                    "MR_SOURCE_BRANCH": ctx.source_branch,
                    "MR_LAST_REVIEWED_SHA": ctx.last_reviewed_sha or "",
                },
            }
        }
    })
```

**`run_review(ctx)` 實作：**
```python
def run_review(self, ctx: MRContext) -> str:
    prompt = _build_initial_prompt(ctx)
    mcp_config = _build_mcp_config(ctx)

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--mcp-config", mcp_config,
        "--dangerously-skip-permissions",
    ]
    if ctx.cli_session_id:
        cmd += ["--resume", ctx.cli_session_id]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error("[claude_cli] CLI error: %s", result.stderr)
        return f"Claude CLI 執行失敗：{result.stderr}"

    data = json.loads(result.stdout)
    ctx.cli_session_id = data.get("session_id")   # 寫回 ctx，供 task_manager 存 Redis
    return data.get("result", "")
```

---

#### Step 12-5：`app/ai_review.py` — factory 新增 claude_cli case

```python
from app.providers.claude_cli import ClaudeCliReviewer

def get_reviewer() -> BaseReviewer:
    if config.AI_PROVIDER == "openai":
        return OpenAIReviewer(config.OPENAI_API_KEY, config.AI_MODEL)
    if config.AI_PROVIDER == "claude_cli":
        return ClaudeCliReviewer()
    return AnthropicReviewer(config.ANTHROPIC_API_KEY, config.AI_MODEL)
```

注意：`ClaudeCliReviewer` 不需要 API key 參數（由 claude CLI 自行讀取環境變數 `ANTHROPIC_API_KEY`）。

---

#### Step 12-6：`app/task_manager.py` — session_id 讀取與寫入

在 `_review_task(ctx)` 中新增 session_id 的讀寫邏輯：

**review 前（讀取 session_id）：**
```python
# 在差異 review 判斷之後、ai_review.run_review() 之前
if config.AI_PROVIDER == "claude_cli":
    ctx.cli_session_id = await redis_client.get_session_id(ctx.project_id, ctx.mr_iid)
    if ctx.cli_session_id:
        logger.info("Resuming CLI session %s for MR %s/%s",
                    ctx.cli_session_id[:8], ctx.project_id, ctx.mr_iid)
```

**review 後（寫入 session_id）：**
```python
# 在 post_mr_comment 之後、set_processed_sha 之前
if config.AI_PROVIDER == "claude_cli" and ctx.cli_session_id:
    await redis_client.set_session_id(ctx.project_id, ctx.mr_iid, ctx.cli_session_id)
```

同時調整 API key 檢查邏輯（`claude_cli` 使用 `ANTHROPIC_API_KEY`）：

> ⚠️ **邊界情況**：現有邏輯是 `ANTHROPIC_API_KEY if anthropic else OPENAI_API_KEY`，若 `AI_PROVIDER=claude_cli` 會錯誤走入 else，抓到空的 `OPENAI_API_KEY`，導致誤判「key 未設定」並 sleep 10 秒 + 發留言。必須修正為：

```python
if config.AI_PROVIDER == "openai":
    api_key = config.OPENAI_API_KEY
else:
    # anthropic 和 claude_cli 都使用 ANTHROPIC_API_KEY
    api_key = config.ANTHROPIC_API_KEY
```

---

#### Step 12-7：`app/webhook.py` — MR merge 清除 session

在 `handle_webhook` 中新增 merge action 的處理（在現有 action 過濾之前）：

```python
if action == "merge":
    project_id = payload["project"]["id"]
    mr_iid = payload["object_attributes"]["iid"]
    await redis_client.delete_session_id(project_id, mr_iid)
    logger.info("Cleared CLI session for merged MR %s/%s", project_id, mr_iid)
    return {"status": "ok", "reason": "session cleared on merge"}
```

注意：merge 不觸發 review，只做 session 清除，因此放在 `_ALLOWED_ACTIONS` 判斷之前處理。

---

#### Step 12-8：`Dockerfile` — 安裝 Node.js、Claude CLI，並建立非 root 使用者

完整 Dockerfile（含 non-root 使用者設定）：

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y git nodejs npm && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# 建立非 root 使用者（--dangerously-skip-permissions 禁止在 root 下執行）
RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# 建立 /data/repos 目錄並設定 owner（volume 掛載後 appuser 有寫入權限）
RUN mkdir -p /data/repos && chown appuser:appuser /data/repos

USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

說明：
- `useradd -m -u 1000 appuser`：建立 home 目錄（`/home/appuser/`），Claude CLI session 存放於此
- `chown appuser:appuser /data/repos`：需在切換使用者前設定，否則無權限
- `USER appuser`：之後所有指令（含 CMD）均以 appuser 執行

---

#### Step 12-11：`docker-compose.yml` — 新增 claude_data volume

在 `webhook` 服務的 `volumes` 中新增，並在頂層 `volumes` 宣告：

```yaml
services:
  webhook:
    volumes:
      - repos:/data/repos
      - claude_data:/home/appuser/.claude  # 新增：持久化 Claude CLI session

volumes:
  repos:
  redis_data:
  claude_data:  # 新增
```

說明：
- `/home/appuser/.claude/` 是 Claude CLI 儲存認證 session 的目錄
- 使用 named volume（`claude_data`）確保容器重建後 session 不遺失
- 登入方式：`docker exec -it <container_name> claude login`（登入後 session 持久化）

---

#### Step 12-12：`Dockerfile` — 設定 git safe.directory

在 `USER appuser` 之後新增：

```dockerfile
USER appuser

RUN git config --global --add safe.directory '*'  # 新增
```

說明：
- git 基於安全考量，當目錄 owner 與執行者不符時（如 volume-mounted repo 由 root 建立，但以 appuser 執行），會拒絕操作並回傳 "detected dubious ownership" 錯誤
- 設定 `safe.directory '*'` 允許 appuser 操作任何目錄，解除此限制
- 必須在 `USER appuser` 之後執行，確保寫入 appuser 的 `~/.gitconfig`（`/home/appuser/.gitconfig`）

---

#### Step 12-9：`requirements.txt` — 新增 fastmcp

```
fastapi
uvicorn[standard]
redis[asyncio]
anthropic
openai
requests
python-dotenv
fastmcp          ← 新增
```

---

#### Step 12-10：`.env.example` — 新增 claude_cli 說明

新增：
```
# AI Provider 設定（可選值：anthropic / openai / claude_cli）
AI_PROVIDER=anthropic

# claude_cli provider 使用 ANTHROPIC_API_KEY，不需另外設定
```

---

### Step 13：擴充 /health endpoint（功能 4.8）

> 在 Step 12 完成的基礎上進行。

---

#### Step 13-1：`app/main.py` — 擴充 `/health` endpoint

修改現有 `/health` endpoint，依 `AI_PROVIDER` 決定是否執行 Claude CLI 健康診斷：

```python
import asyncio
import json
import subprocess

from app import config

@app.get("/health")
async def health():
    if config.AI_PROVIDER != "claude_cli":
        return {"status": "ok"}

    def _check_cli():
        result = subprocess.run(
            ["claude", "-p", "say hi", "--output-format", "json",
             "--dangerously-skip-permissions"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr[:200]}
        try:
            data = json.loads(result.stdout)
            return {"status": "ok", "response": data.get("result", "")}
        except json.JSONDecodeError:
            return {"status": "error", "error": f"Invalid JSON: {result.stdout[:200]}"}

    cli_result = await asyncio.to_thread(_check_cli)
    return {"status": "ok", "claude_cli": cli_result}
```

說明：
- `AI_PROVIDER != "claude_cli"` 時不執行 CLI 測試，維持原行為
- `asyncio.to_thread` 確保 subprocess 不阻塞 async event loop
- 解析 JSON `result` 欄位作為回應文字；失敗時回傳截斷的錯誤訊息

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
| Step 0 | `app/providers/`、`ai_review.py`、`config.py`、`task_manager.py` | 功能 4.5 |
| Step 11 | `gitlab_client.py`、`mr_info.py`、`tools.py`、`task_manager.py`、`providers/*.py` | 功能 4.6 |
| Step 12 | `mr_info.py`、`redis_client.py`、`mcp_server.py`、`providers/claude_cli.py`、`ai_review.py`、`task_manager.py`、`webhook.py`、`Dockerfile`、`requirements.txt` | 功能 4.7 |
