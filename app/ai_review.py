from app import config
from app.mr_info import MRContext
from app.providers.base import BaseReviewer
from app.providers.anthropic import AnthropicReviewer
from app.providers.openai import OpenAIReviewer
from app.providers.claude_cli import ClaudeCliReviewer


def get_reviewer() -> BaseReviewer:
    if config.AI_PROVIDER == "openai":
        return OpenAIReviewer(config.OPENAI_API_KEY, config.AI_MODEL)
    if config.AI_PROVIDER == "claude_cli":
        return ClaudeCliReviewer()
    return AnthropicReviewer(config.ANTHROPIC_API_KEY, config.AI_MODEL)


def run_review(ctx: MRContext) -> str:
    return get_reviewer().run_review(ctx)
