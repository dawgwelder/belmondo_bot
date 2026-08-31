"""Typed runtime and balance settings for Spy Clicker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import AgentCost, AgentType, EventWeight, ExchangeRecipe


AGENT_TYPES: dict[str, AgentType] = {
    agent.id: agent
    for agent in (
        AgentType("informant", 1, "Осведомитель", "🕵️"),
        AgentType("operative", 2, "Оперативник", "🎯"),
        AgentType("observer", 2, "Наблюдатель", "🔭"),
        AgentType("courier", 2, "Курьер", "📨"),
        AgentType("analyst", 3, "Аналитик", "🧠"),
        AgentType("saboteur", 3, "Саботажник", "💣"),
        AgentType("sleeper", 3, "Спящий агент", "💤"),
        AgentType("double_agent", 3, "Двойной агент", "🎭"),
    )
}

DEFAULT_EVENT_WEIGHTS = (
    EventWeight("recruitment", 4),
    EventWeight("handler", 1),
)

DEFAULT_HANDLER_RECIPES = (
    ExchangeRecipe(
        id="tier2",
        display_name="Случайный Tier 2 · 10 осведомителей",
        costs=(AgentCost("informant", 10),),
        reward_pool=("operative", "observer", "courier"),
    ),
    ExchangeRecipe(
        id="operative",
        display_name="Оперативник · 15 осведомителей",
        costs=(AgentCost("informant", 15),),
        reward_pool=("operative",),
    ),
    ExchangeRecipe(
        id="observer",
        display_name="Наблюдатель · 15 осведомителей",
        costs=(AgentCost("informant", 15),),
        reward_pool=("observer",),
    ),
    ExchangeRecipe(
        id="courier",
        display_name="Курьер · 15 осведомителей",
        costs=(AgentCost("informant", 15),),
        reward_pool=("courier",),
    ),
    ExchangeRecipe(
        id="tier3",
        display_name="Случайный Tier 3 · по 2 агента Tier 2",
        costs=(
            AgentCost("operative", 2),
            AgentCost("observer", 2),
            AgentCost("courier", 2),
        ),
        reward_pool=("analyst", "saboteur", "sleeper", "double_agent"),
    ),
)

DEFAULT_PRESTIGE_COSTS = (
    AgentCost("operative", 1),
    AgentCost("observer", 1),
    AgentCost("courier", 1),
)


@dataclass(frozen=True)
class ActivityBand:
    minimum_score: float
    minimum_delay_seconds: int
    maximum_delay_seconds: int


DEFAULT_ACTIVITY_BANDS = (
    ActivityBand(30.0, 12 * 60, 25 * 60),
    ActivityBand(15.0, 25 * 60, 45 * 60),
    ActivityBand(6.0, 45 * 60, 75 * 60),
)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return default if value is None else int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _allowed_chat_ids() -> frozenset[int]:
    raw = os.getenv("SPY_GAME_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return frozenset()
    try:
        return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())
    except ValueError as error:
        raise ValueError(
            "SPY_GAME_ALLOWED_CHAT_IDS must contain integer chat IDs"
        ) from error


@dataclass(frozen=True)
class SpySettings:
    mode: str
    enabled: bool
    database_path: Path
    allowed_chat_ids: frozenset[int]
    tick_seconds: int = 30
    event_lifetime_seconds: int = 3 * 60
    activity_half_life_seconds: int = 30 * 60
    activity_threshold: float = 6.0
    activity_message_points: float = 1.0
    activity_user_debounce_seconds: int = 20
    activity_after_spawn_ratio: float = 0.45
    max_activity_score: float = 100.0
    allow_manual_spawn: bool = False
    llm_narrator_enabled: bool = False
    llm_narrator_timeout_seconds: int = 8
    recruitment_agent_type: str = "informant"
    reputation_reward_cap: int = 5
    event_weights: tuple[EventWeight, ...] = field(
        default_factory=lambda: DEFAULT_EVENT_WEIGHTS
    )
    handler_recipes: tuple[ExchangeRecipe, ...] = field(
        default_factory=lambda: DEFAULT_HANDLER_RECIPES
    )
    prestige_base_costs: tuple[AgentCost, ...] = field(
        default_factory=lambda: DEFAULT_PRESTIGE_COSTS
    )
    activity_bands: tuple[ActivityBand, ...] = field(
        default_factory=lambda: DEFAULT_ACTIVITY_BANDS
    )

    def __post_init__(self) -> None:
        if self.mode not in {"dev", "prod"}:
            raise ValueError("mode must be dev or prod")
        if self.tick_seconds <= 0 or self.event_lifetime_seconds <= 0:
            raise ValueError("tick and event lifetime must be positive")
        if self.activity_half_life_seconds <= 0 or self.activity_threshold <= 0:
            raise ValueError("activity settings must be positive")
        if self.llm_narrator_timeout_seconds <= 0:
            raise ValueError("LLM narrator timeout must be positive")
        if not 0 <= self.activity_after_spawn_ratio < 1:
            raise ValueError("activity_after_spawn_ratio must be in [0, 1)")
        if self.recruitment_agent_type not in AGENT_TYPES:
            raise ValueError("unknown recruitment agent type")
        if self.enabled and not self.allowed_chat_ids:
            raise ValueError("enabled Spy Game requires a non-empty chat allowlist")
        event_types = {item.event_type for item in self.event_weights}
        if event_types != {"recruitment", "handler"}:
            raise ValueError("event weights must configure recruitment and handler")
        if any(item.weight <= 0 for item in self.event_weights):
            raise ValueError("event weights must be positive")
        recipe_ids = [recipe.id for recipe in self.handler_recipes]
        if not recipe_ids or len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("handler recipe IDs must be non-empty and unique")
        for recipe in self.handler_recipes:
            self._validate_costs(recipe.costs)
            if recipe.reward_amount <= 0 or not recipe.reward_pool:
                raise ValueError("handler recipe rewards must be positive")
            if any(agent_id not in AGENT_TYPES for agent_id in recipe.reward_pool):
                raise ValueError("handler recipe contains an unknown reward agent")
        self._validate_costs(self.prestige_base_costs)
        for band in self.activity_bands:
            if band.minimum_delay_seconds <= 0:
                raise ValueError("activity band delay must be positive")
            if band.maximum_delay_seconds < band.minimum_delay_seconds:
                raise ValueError("activity band maximum delay is too small")

    @staticmethod
    def _validate_costs(costs: tuple[AgentCost, ...]) -> None:
        if not costs or any(cost.amount <= 0 for cost in costs):
            raise ValueError("agent costs must be non-empty and positive")
        ids = [cost.agent_type for cost in costs]
        if len(ids) != len(set(ids)) or any(item not in AGENT_TYPES for item in ids):
            raise ValueError("agent costs must contain unique known agent types")

    def handler_recipe(self, recipe_id: str) -> ExchangeRecipe | None:
        return next(
            (recipe for recipe in self.handler_recipes if recipe.id == recipe_id),
            None,
        )

    def prestige_costs(self, reputation: int) -> tuple[AgentCost, ...]:
        multiplier = reputation + 1
        return tuple(
            AgentCost(cost.agent_type, cost.amount * multiplier)
            for cost in self.prestige_base_costs
        )

    @classmethod
    def from_env(cls, mode: str) -> "SpySettings":
        default_path = (
            "var/spy-game-dev.sqlite3" if mode == "dev" else "var/spy-game.sqlite3"
        )
        return cls(
            mode=mode,
            enabled=_env_bool("SPY_GAME_ENABLED", False),
            database_path=Path(os.getenv("SPY_GAME_DB_PATH", default_path)),
            allowed_chat_ids=_allowed_chat_ids(),
            tick_seconds=_env_int("SPY_GAME_TICK_SECONDS", 30),
            event_lifetime_seconds=_env_int("SPY_GAME_EVENT_LIFETIME_SECONDS", 3 * 60),
            allow_manual_spawn=_env_bool("SPY_GAME_ALLOW_MANUAL_SPAWN", mode == "dev"),
            llm_narrator_enabled=_env_bool("SPY_GAME_LLM_NARRATOR_ENABLED", False),
            llm_narrator_timeout_seconds=_env_int(
                "SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS", 8
            ),
        )

    def chat_is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids
