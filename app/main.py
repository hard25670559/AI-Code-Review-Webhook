import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.webhook import router as webhook_router
from app import redis_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await redis_client.get_redis()
    yield


app = FastAPI(title="AI Code Review Webhook", lifespan=lifespan)
app.include_router(webhook_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
