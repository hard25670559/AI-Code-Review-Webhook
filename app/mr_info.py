import asyncio
from dataclasses import dataclass

from app.repo_manager import ensure_repo, _run_git


@dataclass
class MRContext:
    project_id: int
    mr_iid: int
    sha: str
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    repo_url: str
    repo_path: str
    changed_files: list[str]
    last_reviewed_sha: str | None = None


async def build_mr_context(payload: dict) -> MRContext:
    attrs = payload["object_attributes"]
    project_id = payload["project"]["id"]
    repo_url = payload["project"]["http_url"]

    mr_iid = attrs["iid"]
    sha = attrs["last_commit"]["id"]
    title = attrs.get("title", "")
    description = attrs.get("description", "") or ""
    author = attrs["last_commit"]["author"]["name"]
    source_branch = attrs["source_branch"]
    target_branch = attrs["target_branch"]

    repo_path = await ensure_repo(project_id, repo_url)

    raw = await asyncio.to_thread(
        _run_git,
        ["diff", "--name-only", f"origin/{target_branch}...origin/{source_branch}"],
        repo_path,
    )
    changed_files = [f for f in raw.strip().splitlines() if f]

    return MRContext(
        project_id=project_id,
        mr_iid=mr_iid,
        sha=sha,
        title=title,
        description=description,
        author=author,
        source_branch=source_branch,
        target_branch=target_branch,
        repo_url=repo_url,
        repo_path=repo_path,
        changed_files=changed_files,
    )
