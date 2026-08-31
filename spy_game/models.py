"""Telegram-independent domain models for Spy Clicker."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ClaimStatus(str, Enum):
    WON = "won"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    INVALID_ACTION = "invalid_action"
    DISABLED = "disabled"


class EconomyStatus(str, Enum):
    SUCCESS = "success"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    INVALID_RECIPE = "invalid_recipe"
    STALE = "stale"
    DISABLED = "disabled"


@dataclass(frozen=True)
class AgentType:
    id: str
    tier: int
    display_name: str
    emoji: str


@dataclass(frozen=True)
class Reward:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class AgentCost:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class ExchangeRecipe:
    id: str
    display_name: str
    costs: tuple[AgentCost, ...]
    reward_pool: tuple[str, ...]
    reward_amount: int = 1


@dataclass(frozen=True)
class EventWeight:
    event_type: str
    weight: int


@dataclass(frozen=True)
class SpawnEvent:
    event_id: str
    chat_id: int
    event_type: str
    expires_at: datetime


@dataclass(frozen=True)
class ExpiredEvent:
    event_id: str
    chat_id: int
    message_id: int | None


@dataclass(frozen=True)
class TickResult:
    spawned: tuple[SpawnEvent, ...] = ()
    expired: tuple[ExpiredEvent, ...] = ()


@dataclass(frozen=True)
class ClaimResult:
    status: ClaimStatus
    event_id: str
    reward: Reward | None = None
    winner_user_id: int | None = None


@dataclass(frozen=True)
class ExchangeResult:
    status: EconomyStatus
    event_id: str
    recipe_id: str
    reward: Reward | None = None
    required: tuple[AgentCost, ...] = ()


@dataclass(frozen=True)
class PrestigeResult:
    status: EconomyStatus
    reputation: int
    required: tuple[AgentCost, ...] = ()


@dataclass(frozen=True)
class AgentHolding:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class Profile:
    user_id: int
    username: str | None
    display_name: str | None
    reputation: int
    agency_level: int
    total_agents: int


@dataclass(frozen=True)
class ChatStatus:
    chat_id: int
    enabled: bool
    activity_score: float
    next_event_at: datetime | None
    active_event_id: str | None
    active_event_expires_at: datetime | None


@dataclass(frozen=True)
class AdminResult:
    ok: bool
    message: str
    event: SpawnEvent | None = None
    message_id_to_close: int | None = None
