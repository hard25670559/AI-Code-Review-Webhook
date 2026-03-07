import logging

import anthropic

from app.mr_info import MRContext
from app.providers.base import BaseReviewer
from app import tools

logger = logging.getLogger(__name__)


def _build_initial_prompt(ctx: MRContext) -> str:
    file_list = "\n".join(ctx.changed_files) if ctx.changed_files else "（無變動檔案）"
    description = ctx.description.strip() if ctx.description else "（無描述）"
    return (
        f"以下是一個 GitLab Merge Request 的資訊，請進行 code review：\n\n"
        f"標題：{ctx.title}\n"
        f"作者：{ctx.author}\n"
        f"Target Branch：{ctx.target_branch}\n"
        f"Source Branch：{ctx.source_branch}\n\n"
        f"MR 描述：\n{description}\n\n"
        f"變動檔案列表：\n{file_list}\n\n"
        f"請主動使用工具查看需要的檔案 diff 與相關內容，完成後提供詳細的 code review 建議。"
    )


_TOOL_SCHEMAS = [
    {
        "name": "get_file_diff",
        "description": "取得該 MR 中某個特定檔案的 diff 內容，查看此檔案在本次 MR 中的完整變更。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要查看 diff 的檔案路徑"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "get_file_content",
        "description": "取得專案中某個檔案的完整內容（source branch 的現有版本），用於查看相關實作、interface、base class 等完整上下文。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要讀取的檔案路徑"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_directory",
        "description": "列出專案中某個目錄的結構，了解專案組織方式。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的目錄路徑，根目錄請傳空字串"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_in_repo",
        "description": "在專案中搜尋關鍵字，找出相關函數定義、類別、常數等，最多回傳 5 筆結果。",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "要搜尋的關鍵字"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_issue",
        "description": "取得 GitLab Issue 的詳細內容（標題、描述、狀態、標籤），作為判斷 MR 是否符合需求的標準。",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_iid": {"type": "integer", "description": "Issue 在該專案中的編號"},
            },
            "required": ["issue_iid"],
        },
    },
    {
        "name": "get_issue_notes",
        "description": "取得 GitLab Issue 底下的留言列表，查看需求討論內容與決策背景。",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_iid": {"type": "integer", "description": "Issue 在該專案中的編號"},
            },
            "required": ["issue_iid"],
        },
    },
]


class AnthropicReviewer(BaseReviewer):
    def __init__(self, api_key: str, model: str):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def run_review(self, ctx: MRContext) -> str:
        logger.info("[anthropic/%s] Starting review for MR %s/%s (%s)", self._model, ctx.project_id, ctx.mr_iid, ctx.sha[:7])
        messages = [{"role": "user", "content": _build_initial_prompt(ctx)}]
        tool_call_count = 0

        while True:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                tools=_TOOL_SCHEMAS,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        logger.info("[anthropic/%s] Review complete: %d tool calls, %d chars", self._model, tool_call_count, len(block.text))
                        return block.text
                return ""

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info("Tool call: %s(%s)", block.name, ", ".join(f"{k}={v!r}" for k, v in block.input.items()))
                        tool_call_count += 1
                        result = tools.dispatch_tool(ctx, block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        return ""
