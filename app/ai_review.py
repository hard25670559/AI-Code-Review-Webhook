from app import config
from app.mr_info import MRContext
from app.providers.base import BaseReviewer
from app.providers.anthropic import AnthropicReviewer
from app.providers.openai import OpenAIReviewer


def get_reviewer() -> BaseReviewer:
    if config.AI_PROVIDER == "openai":
        return OpenAIReviewer(config.OPENAI_API_KEY, config.AI_MODEL)
    return AnthropicReviewer(config.ANTHROPIC_API_KEY, config.AI_MODEL)


def run_review(ctx: MRContext) -> str:
    return get_reviewer().run_review(ctx)
