import redis.asyncio as aioredis
from app import config

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.Redis(host=config.REDIS_HOST, decode_responses=True)
    return _redis


async def get_processed_sha(project_id: int, mr_iid: int) -> str | None:
    r = await get_redis()
    return await r.get(f"ai_review:{project_id}:{mr_iid}")


async def set_processed_sha(project_id: int, mr_iid: int, sha: str) -> None:
    r = await get_redis()
    await r.set(f"ai_review:{project_id}:{mr_iid}", sha)


async def get_session_id(project_id: int, mr_iid: int) -> str | None:
    r = await get_redis()
    return await r.get(f"ai_review:session:{project_id}:{mr_iid}")


async def set_session_id(project_id: int, mr_iid: int, session_id: str) -> None:
    r = await get_redis()
    await r.set(f"ai_review:session:{project_id}:{mr_iid}", session_id)


async def delete_session_id(project_id: int, mr_iid: int) -> None:
    r = await get_redis()
    await r.delete(f"ai_review:session:{project_id}:{mr_iid}")
