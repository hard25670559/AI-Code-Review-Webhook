import os
import subprocess

from app import config
from app import gitlab_client
from app.repo_manager import _run_git

_TRUNCATE = 3000


def _truncate(text: str) -> str:
    if len(text) <= _TRUNCATE:
        return text
    return text[:_TRUNCATE] + f"\n... [截斷，共 {len(text)} 字元]"


def get_file_diff(project_id: int, file_path: str, target_branch: str, source_branch: str) -> str:
    repo_path = os.path.join(config.REPO_BASE_PATH, str(project_id))
    try:
        output = _run_git(
            ["diff", f"{target_branch}...{source_branch}", "--", file_path],
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


TOOL_SCHEMAS = [
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
