import logging
import subprocess

from fastapi import APIRouter, HTTPException, Request, BackgroundTasks

from app import config, redis_client, task_manager
from app.mr_info import build_mr_context

logger = logging.getLogger(__name__)
router = APIRouter()

_ALLOWED_ACTIONS = {"open", "reopen", "update"}


@router.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    token = request.headers.get("X-Gitlab-Token", "")
    if token != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = await request.json()

    if payload.get("object_kind") != "merge_request":
        return {"status": "ignored", "reason": "not a merge_request event"}

    action = payload.get("object_attributes", {}).get("action", "")

    if action == "merge":
        project_id = payload["project"]["id"]
        mr_iid = payload["object_attributes"]["iid"]
        await redis_client.delete_session_id(project_id, mr_iid)
        logger.info("Cleared CLI session for merged MR %s/%s", project_id, mr_iid)
        return {"status": "ok", "reason": "session cleared on merge"}

    if action not in _ALLOWED_ACTIONS:
        return {"status": "ignored", "reason": f"action '{action}' not handled"}

    sha = payload["object_attributes"]["last_commit"]["id"]
    project_id = payload["project"]["id"]
    mr_iid = payload["object_attributes"]["iid"]

    processed_sha = await redis_client.get_processed_sha(project_id, mr_iid)
    if processed_sha == sha:
        logger.info("SHA %s already processed for MR %s/%s, skipping", sha[:7], project_id, mr_iid)
        return {"status": "ignored", "reason": "sha already processed"}

    background_tasks.add_task(_process_mr, payload)
    return {"status": "accepted"}


async def _process_mr(payload: dict) -> None:
    try:
        ctx = await build_mr_context(payload)
        task_manager.submit_review_task(ctx)
    except subprocess.CalledProcessError as e:
        logger.error("Git command failed: %s\nstderr: %s", " ".join(e.cmd), e.stderr)
    except Exception:
        logger.exception("Failed to process MR payload")
