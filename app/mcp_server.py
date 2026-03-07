import os

from mcp.server.fastmcp import FastMCP

from app import tools
from app.mr_info import MRContext

_project_id = int(os.environ["MR_PROJECT_ID"])
_mr_iid = int(os.environ["MR_IID"])
_sha = os.environ["MR_SHA"]
_target_branch = os.environ["MR_TARGET_BRANCH"]
_source_branch = os.environ["MR_SOURCE_BRANCH"]
_last_sha = os.environ.get("MR_LAST_REVIEWED_SHA") or None


def _make_ctx() -> MRContext:
    return MRContext(
        project_id=_project_id,
        mr_iid=_mr_iid,
        sha=_sha,
        title="",
        description="",
        author="",
        source_branch=_source_branch,
        target_branch=_target_branch,
        repo_url="",
        repo_path="",
        changed_files=[],
        last_reviewed_sha=_last_sha,
    )


mcp = FastMCP("ai-review")


@mcp.tool()
def get_file_diff(file_path: str) -> str:
    """取得該 MR 中某個特定檔案的 diff 內容，查看此檔案在本次 MR 中的完整變更。"""
    return tools.get_file_diff(_project_id, file_path, _target_branch, _source_branch)


@mcp.tool()
def get_file_content(file_path: str) -> str:
    """取得專案中某個檔案的完整內容（source branch 的現有版本），用於查看相關實作、interface、base class 等完整上下文。"""
    return tools.get_file_content(_project_id, file_path)


@mcp.tool()
def list_directory(path: str) -> str:
    """列出專案中某個目錄的結構，了解專案組織方式。根目錄請傳空字串。"""
    return tools.list_directory(_project_id, path)


@mcp.tool()
def search_in_repo(keyword: str) -> str:
    """在專案中搜尋關鍵字，找出相關函數定義、類別、常數等，最多回傳 5 筆結果。"""
    return tools.search_in_repo(_project_id, keyword)


@mcp.tool()
def get_issue(issue_iid: int) -> str:
    """取得 GitLab Issue 的詳細內容（標題、描述、狀態、標籤），作為判斷 MR 是否符合需求的標準。"""
    return tools.get_issue(_project_id, issue_iid)


@mcp.tool()
def get_issue_notes(issue_iid: int) -> str:
    """取得 GitLab Issue 底下的留言列表，查看需求討論內容與決策背景。"""
    return tools.get_issue_notes(_project_id, issue_iid)


@mcp.tool()
def get_diff_between_shas(from_sha: str, to_sha: str) -> str:
    """取得兩個 commit SHA 之間的 diff，用於查看自上次 review 後新增的變更。"""
    return tools.get_diff_between_shas(_make_ctx(), from_sha, to_sha)


@mcp.tool()
def get_previous_review() -> str:
    """取得此 MR 上一次 AI review 的留言內容，作為理解新 diff 的背景參考。若覺得對理解新變更有幫助才需呼叫。"""
    return tools.get_previous_review(_make_ctx())


if __name__ == "__main__":
    mcp.run(transport="stdio")
