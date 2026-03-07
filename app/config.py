import os
from dotenv import load_dotenv

load_dotenv()

GITLAB_URL = os.environ["GITLAB_URL"].rstrip("/")
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REPO_BASE_PATH = os.getenv("REPO_BASE_PATH", "/data/repos")

AI_PROVIDER = os.getenv("AI_PROVIDER", "anthropic")
AI_MODEL = os.getenv("AI_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
