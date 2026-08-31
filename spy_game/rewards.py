"""Server-side reward resolution for Spy Clicker events."""

from __future__ import annotations

from .models import DropEntry, DropReward, ExchangeRecipe, NpcRecipe, Reward
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

    def resolve_dead_drop(self, rng: RandomSource) -> DropReward:
        return self._resolve_drop(self.settings.dead_drop_entries, rng)

    def resolve_npc(
        self,
        recipe: NpcRecipe,
        agency_level: int,
        rng: RandomSource,
    ) -> DropReward:
        entries = recipe.rewards
        if recipe.npc_id == "recruiter" and agency_level > 0:
            weights = [entry.weight for entry in entries]
            common = [
                index
                for index, entry in enumerate(entries)
                if entry.reward_type == "agent"
                and self.settings.agent_tier(entry.reward_id) == 2
            ]
            rare = [
                index
                for index, entry in enumerate(entries)
                if entry.reward_type == "agent"
                and self.settings.agent_tier(entry.reward_id) >= 4
            ]
            if common and rare:
                bonus = min(
                    agency_level * self.settings.agency_rare_bonus_percent,
                    sum(weights[index] - 1 for index in common),
                )
                for offset in range(bonus):
                    weights[common[offset % len(common)]] -= 1
                    weights[rare[offset % len(rare)]] += 1
                entries = tuple(
                    DropEntry(
                        entry.reward_type,
                        entry.reward_id,
                        entry.amount,
                        weights[index],
                    )
                    for index, entry in enumerate(entries)
                )
        return self._resolve_drop(entries, rng)

    @staticmethod
    def _resolve_drop(
        entries: tuple[DropEntry, ...],
        rng: RandomSource,
    ) -> DropReward:
        roll = rng.randint(1, sum(entry.weight for entry in entries))
        cumulative = 0
        for entry in entries:
            cumulative += entry.weight
            if roll <= cumulative:
                return DropReward(
                    entry.reward_type,
                    entry.reward_id,
                    entry.amount,
                )
        raise RuntimeError("dead drop reward selection failed")
