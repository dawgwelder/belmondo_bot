"""Server-side reward resolution for Spy Clicker events."""

from __future__ import annotations

from .models import ExchangeRecipe, Reward
from .scheduler import RandomSource
from .settings import SpySettings


class RewardResolver:
    def __init__(self, settings: SpySettings) -> None:
        self.settings = settings

    def resolve(self, event_type: str, reputation: int) -> Reward:
        if event_type != "recruitment":
            raise ValueError(f"unsupported reward event: {event_type}")
        amount = 1 + min(reputation, self.settings.reputation_reward_cap)
        return Reward(self.settings.recruitment_agent_type, amount)

    @staticmethod
    def resolve_exchange(recipe: ExchangeRecipe, rng: RandomSource) -> Reward:
        index = rng.randint(0, len(recipe.reward_pool) - 1)
        return Reward(recipe.reward_pool[index], recipe.reward_amount)
