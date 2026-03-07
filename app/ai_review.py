import anthropic

from app import config
from app.mr_info import MRContext
from app import tools

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


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


def _dispatch_tool(ctx: MRContext, tool_name: str, tool_input: dict) -> str:
    if tool_name == "get_file_diff":
        return tools.get_file_diff(
            ctx.project_id,
            tool_input["file_path"],
            ctx.target_branch,
            ctx.source_branch,
        )
    elif tool_name == "get_file_content":
        return tools.get_file_content(ctx.project_id, tool_input["file_path"])
    elif tool_name == "list_directory":
        return tools.list_directory(ctx.project_id, tool_input["path"])
    elif tool_name == "search_in_repo":
        return tools.search_in_repo(ctx.project_id, tool_input["keyword"])
    elif tool_name == "get_issue":
        return tools.get_issue(ctx.project_id, tool_input["issue_iid"])
    elif tool_name == "get_issue_notes":
        return tools.get_issue_notes(ctx.project_id, tool_input["issue_iid"])
    else:
        return f"未知工具：{tool_name}"


def run_review(ctx: MRContext) -> str:
    messages = [{"role": "user", "content": _build_initial_prompt(ctx)}]

    while True:
        response = _client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            tools=tools.TOOL_SCHEMAS,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _dispatch_tool(ctx, block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return ""
