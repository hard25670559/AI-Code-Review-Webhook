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
    gitlab_parsed = urlparse(config.GITLAB_URL)
    return parsed._replace(
        scheme=gitlab_parsed.scheme,
        netloc=f"oauth2:{config.GITLAB_TOKEN}@{gitlab_parsed.netloc}",
    ).geturl()


def _run_git(args: list[str], cwd: str | None = None) -> str:
    env = os.environ.copy()
    env["GIT_SSL_NO_VERIFY"] = "1"
    result = subprocess.run(
        ["git", "-c", "http.sslVerify=false", "-c", "credential.helper="] + args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
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
