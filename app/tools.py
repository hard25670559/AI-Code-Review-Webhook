import os
import subprocess

from app import config
from app import gitlab_client
from app.repo_manager import _run_git
from app.mr_info import MRContext

_TRUNCATE = 3000


def _truncate(text: str) -> str:
    if len(text) <= _TRUNCATE:
        return text
    return text[:_TRUNCATE] + f"\n... [截斷，共 {len(text)} 字元]"


def get_file_diff(project_id: int, file_path: str, target_branch: str, source_branch: str) -> str:
    repo_path = os.path.join(config.REPO_BASE_PATH, str(project_id))
    try:
        output = _run_git(
            ["diff", f"origin/{target_branch}...origin/{source_branch}", "--", file_path],
            cwd=repo_path,
        )
        return _truncate(output) if output else "（無差異）"
    except subprocess.CalledProcessError as e:
        return f"錯誤：{e.stderr}"


def get_file_content(project_id: int, file_path: str) -> str:
    full_path = os.path.join(config.REPO_BASE_PATH, str(project_id), file_path)
    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
        return _truncate(content)
    except FileNotFoundError:
        return f"錯誤：找不到檔案 {file_path}"
    except Exception as e:
        return f"錯誤：{e}"


def list_directory(project_id: int, path: str) -> str:
    full_path = os.path.join(config.REPO_BASE_PATH, str(project_id), path)
    try:
        entries = sorted(os.scandir(full_path), key=lambda e: (not e.is_dir(), e.name))
        lines = []
        for entry in entries:
            prefix = "[DIR] " if entry.is_dir() else "[FILE]"
            lines.append(f"{prefix} {entry.name}")
        return _truncate("\n".join(lines)) if lines else "（空目錄）"
    except FileNotFoundError:
        return f"錯誤：找不到目錄 {path}"
    except Exception as e:
        return f"錯誤：{e}"


def search_in_repo(project_id: int, keyword: str) -> str:
    repo_path = os.path.join(config.REPO_BASE_PATH, str(project_id))
    try:
        result = subprocess.run(
            ["grep", "-r", "-n", "--include=*", keyword, "."],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        lines = result.stdout.strip().splitlines()
        top5 = lines[:5]
        output = "\n".join(top5)
        if len(lines) > 5:
            output += f"\n... 共 {len(lines)} 筆，僅顯示前 5 筆"
        return _truncate(output) if output else "（無符合結果）"
    except Exception as e:
        return f"錯誤：{e}"


def get_issue(project_id: int, issue_iid: int) -> str:
    try:
        issue = gitlab_client.get_issue(project_id, issue_iid)
        labels = ", ".join(issue.get("labels", [])) or "（無）"
        text = (
            f"標題：{issue.get('title', '')}\n"
            f"狀態：{issue.get('state', '')}\n"
            f"標籤：{labels}\n\n"
            f"描述：\n{issue.get('description', '') or '（無描述）'}"
        )
        return _truncate(text)
    except Exception as e:
        return f"錯誤：{e}"


def get_issue_notes(project_id: int, issue_iid: int) -> str:
    try:
        notes = gitlab_client.get_issue_notes(project_id, issue_iid)
        lines = []
        for note in notes:
            author = note.get("author", {}).get("name", "未知")
            body = note.get("body", "")
            lines.append(f"[{author}]：{body}")
        output = "\n\n".join(lines)
        return _truncate(output) if output else "（無留言）"
    except Exception as e:
        return f"錯誤：{e}"


def get_diff_between_shas(ctx: MRContext, from_sha: str, to_sha: str) -> str:
    repo_path = os.path.join(config.REPO_BASE_PATH, str(ctx.project_id))
    try:
        output = _run_git(["diff", f"{from_sha}...{to_sha}"], cwd=repo_path)
        return _truncate(output) if output else "（無差異）"
    except subprocess.CalledProcessError as e:
        return f"錯誤：{e.stderr}"


def get_previous_review(ctx: MRContext) -> str:
    try:
        notes = gitlab_client.get_mr_notes(ctx.project_id, ctx.mr_iid)
        for note in notes:
            body = note.get("body", "")
            if body.startswith("## AI Code Review（"):
                return _truncate(body)
        return "（找不到過去的 review 記錄）"
    except Exception as e:
        return f"錯誤：{e}"


def dispatch_tool(ctx: MRContext, tool_name: str, tool_input: dict) -> str:
    if tool_name == "get_file_diff":
        return get_file_diff(ctx.project_id, tool_input["file_path"], ctx.target_branch, ctx.source_branch)
    elif tool_name == "get_file_content":
        return get_file_content(ctx.project_id, tool_input["file_path"])
    elif tool_name == "list_directory":
        return list_directory(ctx.project_id, tool_input["path"])
    elif tool_name == "search_in_repo":
        return search_in_repo(ctx.project_id, tool_input["keyword"])
    elif tool_name == "get_issue":
        return get_issue(ctx.project_id, tool_input["issue_iid"])
    elif tool_name == "get_issue_notes":
        return get_issue_notes(ctx.project_id, tool_input["issue_iid"])
    elif tool_name == "get_diff_between_shas":
        return get_diff_between_shas(ctx, tool_input["from_sha"], tool_input["to_sha"])
    elif tool_name == "get_previous_review":
        return get_previous_review(ctx)
    else:
        return f"未知工具：{tool_name}"
