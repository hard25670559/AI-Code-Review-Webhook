import json
import logging
import subprocess

from app.mr_info import MRContext
from app.providers.base import BaseReviewer

logger = logging.getLogger(__name__)


def _build_initial_prompt(ctx: MRContext) -> str:
    file_list = "\n".join(ctx.changed_files) if ctx.changed_files else "（無變動檔案）"
    description = ctx.description.strip() if ctx.description else "（無描述）"
    base = (
        f"以下是一個 GitLab Merge Request 的資訊，請進行 code review：\n\n"
        f"標題：{ctx.title}\n"
        f"作者：{ctx.author}\n"
        f"Target Branch：{ctx.target_branch}\n"
        f"Source Branch：{ctx.source_branch}\n\n"
        f"MR 描述：\n{description}\n\n"
        f"變動檔案列表：\n{file_list}\n\n"
    )
    if ctx.last_reviewed_sha:
        return (
            base
            + f"此 MR 先前已有過 AI review（SHA：{ctx.last_reviewed_sha[:7]}）。\n"
            f"本次只需針對 {ctx.last_reviewed_sha[:7]} → {ctx.sha[:7]} 之間的新增變更進行補充 review。\n"
            f"請使用 get_diff_between_shas 工具取得差異內容。\n"
            f"如有需要，可使用 get_previous_review 工具查看上次的 review 結論作為參考。\n"
            f"完成後提供針對新增變更的 code review 建議。"
        )
    return base + "請主動使用工具查看需要的檔案 diff 與相關內容，完成後提供詳細的 code review 建議。"


def _build_mcp_config(ctx: MRContext) -> str:
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


class ClaudeCliReviewer(BaseReviewer):
    def run_review(self, ctx: MRContext) -> str:
        logger.info("[claude_cli] Starting review for MR %s/%s (%s)", ctx.project_id, ctx.mr_iid, ctx.sha[:7])
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
            logger.info("[claude_cli] Resuming session %s", ctx.cli_session_id[:8])

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.error("[claude_cli] CLI error: %s", result.stderr[:500])
            return f"Claude CLI 執行失敗：{result.stderr[:500]}"

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.error("[claude_cli] Failed to parse JSON output: %s", result.stdout[:200])
            return result.stdout

        ctx.cli_session_id = data.get("session_id")
        review_text = data.get("result", "")
        logger.info("[claude_cli] Review complete: session=%s, %d chars",
                    ctx.cli_session_id[:8] if ctx.cli_session_id else "None", len(review_text))
        return review_text
