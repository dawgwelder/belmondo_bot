"""Typed runtime and balance settings for Spy Clicker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import AgentType


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
        for band in self.activity_bands:
            if band.minimum_delay_seconds <= 0:
                raise ValueError("activity band delay must be positive")
            if band.maximum_delay_seconds < band.minimum_delay_seconds:
                raise ValueError("activity band maximum delay is too small")

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
