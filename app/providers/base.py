from abc import ABC, abstractmethod

from app.mr_info import MRContext


class BaseReviewer(ABC):
    @abstractmethod
    def run_review(self, ctx: MRContext) -> str: ...
