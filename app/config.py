import os
from dotenv import load_dotenv

load_dotenv()

GITLAB_URL = os.environ["GITLAB_URL"].rstrip("/")
GITLAB_TOKEN = os.environ["GITLAB_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REPO_BASE_PATH = os.getenv("REPO_BASE_PATH", "/data/repos")
