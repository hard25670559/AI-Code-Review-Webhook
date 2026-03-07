import asyncio
import json
import logging
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.webhook import router as webhook_router
from app import config, redis_client

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
    if config.AI_PROVIDER != "claude_cli":
        return {"status": "ok"}

    def _check_cli():
        result = subprocess.run(
            ["claude", "-p", "say hi", "--output-format", "json",
             "--dangerously-skip-permissions"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return {"status": "error", "error": result.stderr[:200]}
        try:
            data = json.loads(result.stdout)
            return {"status": "ok", "response": data.get("result", "")}
        except json.JSONDecodeError:
            return {"status": "error", "error": f"Invalid JSON: {result.stdout[:200]}"}

    cli_result = await asyncio.to_thread(_check_cli)
    return {"status": "ok", "claude_cli": cli_result}
