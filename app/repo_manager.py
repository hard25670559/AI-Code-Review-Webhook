import asyncio
import os
import subprocess
from urllib.parse import urlparse

from app import config

_repo_locks: dict[int, asyncio.Lock] = {}


def _get_lock(project_id: int) -> asyncio.Lock:
    if project_id not in _repo_locks:
        _repo_locks[project_id] = asyncio.Lock()
    return _repo_locks[project_id]


def _repo_path(project_id: int) -> str:
    return os.path.join(config.REPO_BASE_PATH, str(project_id))


def _build_clone_url(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    return parsed._replace(netloc=f"{config.GITLAB_TOKEN}@{parsed.netloc}").geturl()


def _run_git(args: list[str], cwd: str | None = None) -> str:
    env = os.environ.copy()
    env["GIT_SSL_NO_VERIFY"] = "1"
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


async def ensure_repo(project_id: int, repo_url: str) -> str:
    path = _repo_path(project_id)
    lock = _get_lock(project_id)

    async with lock:
        if os.path.isdir(os.path.join(path, ".git")):
            await asyncio.to_thread(_run_git, ["fetch", "--all"], path)
        else:
            os.makedirs(config.REPO_BASE_PATH, exist_ok=True)
            clone_url = _build_clone_url(repo_url)
            await asyncio.to_thread(_run_git, ["clone", clone_url, path])

    return path
