import asyncio
import logging
from datetime import datetime, timezone, timedelta

_TZ_TAIPEI = timezone(timedelta(hours=8))

from app.mr_info import MRContext
from app import ai_review, gitlab_client, redis_client, config

logger = logging.getLogger(__name__)

_AI_REVIEW_PREFIX = "## AI Code Review（"

running_tasks: dict[tuple[int, int], asyncio.Task] = {}


async def _review_task(ctx: MRContext) -> None:
    key = (ctx.project_id, ctx.mr_iid)
    try:
        api_key = config.ANTHROPIC_API_KEY if config.AI_PROVIDER == "anthropic" else config.OPENAI_API_KEY
        if not api_key:
            await asyncio.sleep(10)
            now = datetime.now(_TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
            await asyncio.to_thread(
                gitlab_client.post_mr_comment,
                ctx.project_id,
                ctx.mr_iid,
                f"{config.AI_PROVIDER.upper()}_API_KEY 尚未設定，時間：{now}",
            )
            return

        last_sha = await redis_client.get_processed_sha(ctx.project_id, ctx.mr_iid)
        if last_sha:
            notes = await asyncio.to_thread(gitlab_client.get_mr_notes, ctx.project_id, ctx.mr_iid)
            has_ai_review = any(n.get("body", "").startswith(_AI_REVIEW_PREFIX) for n in notes)
            if has_ai_review:
                ctx.last_reviewed_sha = last_sha
                logger.info("Incremental review mode for MR %s/%s (base: %s)", ctx.project_id, ctx.mr_iid, last_sha[:7])
            else:
                logger.info("AI review comment not found, falling back to full review for MR %s/%s", ctx.project_id, ctx.mr_iid)

        review_text = await asyncio.to_thread(ai_review.run_review, ctx)

        if running_tasks.get(key) is not asyncio.current_task():
            logger.info("Task for MR %s/%s was superseded, skipping comment", ctx.project_id, ctx.mr_iid)
            return

        comment = f"## AI Code Review（{ctx.sha[:7]}）\n\n{review_text}"
        await asyncio.to_thread(gitlab_client.post_mr_comment, ctx.project_id, ctx.mr_iid, comment)
        await redis_client.set_processed_sha(ctx.project_id, ctx.mr_iid, ctx.sha)
        logger.info("Review posted for MR %s/%s (%s)", ctx.project_id, ctx.mr_iid, ctx.sha[:7])

    except asyncio.CancelledError:
        logger.info("Review task for MR %s/%s cancelled", ctx.project_id, ctx.mr_iid)
    except Exception:
        logger.exception("Error during review for MR %s/%s", ctx.project_id, ctx.mr_iid)
    finally:
        if running_tasks.get(key) is asyncio.current_task():
            running_tasks.pop(key, None)


def submit_review_task(ctx: MRContext) -> None:
    key = (ctx.project_id, ctx.mr_iid)

    old_task = running_tasks.get(key)
    if old_task and not old_task.done():
        old_task.cancel()
        logger.info("Cancelled previous review task for MR %s/%s", ctx.project_id, ctx.mr_iid)

    task = asyncio.create_task(_review_task(ctx))
    running_tasks[key] = task
