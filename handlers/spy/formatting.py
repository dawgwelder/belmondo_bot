"""Spy Clicker Telegram formatting."""
from __future__ import annotations

import math
from datetime import datetime
from spy_game.models import AgentCost
from spy_game.settings import AGENT_TYPES, ITEM_TYPES


def _display_name(user) -> str:
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


def _public_username(user) -> str | None:
    if not user.username or not user.username.strip("@"):
        return None
    return f"@{user.username.lstrip('@')}"


def _public_label(user) -> str:
    return _public_username(user) or "Скрытый агент"


def _countdown(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "таймер ещё не назначен"
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        return "событие ожидается в ближайший цикл"
    minutes = max(1, math.ceil(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"примерно через {hours} ч {minutes} мин"
    return f"примерно через {minutes} мин"


def _format_costs(costs: tuple[AgentCost, ...]) -> str:
    return ", ".join(
        f"{AGENT_TYPES[cost.agent_type].display_name} ×{cost.amount}" for cost in costs
    )


def _format_item_costs(costs) -> str:
    return ", ".join(
        f"{ITEM_TYPES[cost.item_type].display_name} ×{cost.amount}" for cost in costs
    )


def _format_drop_reward(reward) -> str:
    registry = AGENT_TYPES if reward.reward_type == "agent" else ITEM_TYPES
    definition = registry[reward.reward_id]
    return f"{definition.emoji} {definition.display_name} ×{reward.amount}"


def _format_npc_recipe_reward(recipe) -> str:
    if len(recipe.rewards) == 1:
        return _format_drop_reward(recipe.rewards[0])
    tiers = sorted(
        {
            AGENT_TYPES[reward.reward_id].tier
            for reward in recipe.rewards
            if reward.reward_type == "agent" and reward.reward_id in AGENT_TYPES
        }
    )
    if len(tiers) > 1:
        return f"🎲 Случайный агент Tier {tiers[0]}–{tiers[-1]}"
    if tiers:
        return f"🎲 Случайный агент Tier {tiers[0]}"
    return "🎲 Случайный результат"
