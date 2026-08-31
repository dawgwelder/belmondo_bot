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


class EquipmentStatus(str, Enum):
    SUCCESS = "success"
    NOT_OWNED = "not_owned"
    NOT_EQUIPMENT = "not_equipment"
    ALREADY_EQUIPPED = "already_equipped"
    NO_FREE_SLOT = "no_free_slot"
    INVALID_SLOT = "invalid_slot"
    NOT_EQUIPPED = "not_equipped"
    DISABLED = "disabled"


class DeathOperationStatus(str, Enum):
    CONFIRMATION_REQUIRED = "confirmation_required"
    WON = "won"
    LOST = "lost"
    INSUFFICIENT_AGENTS = "insufficient_agents"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    INVALID_ACTION = "invalid_action"
    DISABLED = "disabled"


class InterceptStatus(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    INVALID_CHOICE = "invalid_choice"
    DISABLED = "disabled"


class CooperativeStatus(str, Enum):
    CONTRIBUTED = "contributed"
    COMPLETED = "completed"
    ALREADY_CONTRIBUTED = "already_contributed"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    DISABLED = "disabled"


class ChaseStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    DISABLED = "disabled"


class NpcStatus(str, Enum):
    SUCCESS = "success"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    ALREADY_RESOLVED = "already_resolved"
    EXPIRED = "expired"
    NOT_FOUND = "not_found"
    WRONG_CHAT = "wrong_chat"
    INVALID_RECIPE = "invalid_recipe"
    DISABLED = "disabled"


class AgencyStatus(str, Enum):
    SUCCESS = "success"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    STALE = "stale"
    MAX_LEVEL = "max_level"
    DISABLED = "disabled"


class ItemCategory(str, Enum):
    CONSUMABLE = "consumable"
    EQUIPMENT = "equipment"


@dataclass(frozen=True)
class AgentType:
    id: str
    tier: int
    display_name: str
    emoji: str


@dataclass(frozen=True)
class ItemType:
    id: str
    display_name: str
    emoji: str
    category: ItemCategory


@dataclass(frozen=True)
class Reward:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class AgentCost:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class ItemCost:
    item_type: str
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
class InterceptOption:
    id: str
    display_name: str


@dataclass(frozen=True)
class InterceptScenario:
    id: str
    prompt: str
    options: tuple[InterceptOption, ...]
    correct_option_id: str
    reward_item: str
    reward_amount: int = 1


@dataclass(frozen=True)
class DropEntry:
    reward_type: str
    reward_id: str | None
    amount: int
    weight: int


@dataclass(frozen=True)
class NpcRecipe:
    id: str
    npc_id: str
    display_name: str
    agent_costs: tuple[AgentCost, ...]
    item_costs: tuple[ItemCost, ...]
    rewards: tuple[DropEntry, ...]


@dataclass(frozen=True)
class DropReward:
    reward_type: str
    reward_id: str | None
    amount: int


@dataclass(frozen=True)
class SpawnEvent:
    event_id: str
    chat_id: int
    event_type: str
    expires_at: datetime
    config_id: str | None = None
    tone: str = "bureaucratic"
    story_hook: str | None = None
    lore_context: tuple[str, ...] = ()


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
class DirectorState:
    chat_id: int
    activity_score: float
    active_players: int
    minutes_since_last_event: int | None
    recent_events: tuple[str, ...]
    story_arc: str | None
    story_stage: int
    allowed_events: tuple[str, ...]


@dataclass(frozen=True)
class PreparedTick:
    due: tuple[DirectorState, ...] = ()
    expired: tuple[ExpiredEvent, ...] = ()


@dataclass(frozen=True)
class ClaimResult:
    status: ClaimStatus
    event_id: str
    reward: Reward | None = None
    winner_user_id: int | None = None


@dataclass(frozen=True)
class DeadDropResult:
    status: ClaimStatus
    event_id: str
    reward: DropReward | None = None
    winner_user_id: int | None = None


@dataclass(frozen=True)
class DeathOperationResult:
    status: DeathOperationStatus
    event_id: str
    staked: tuple[AgentHolding, ...] = ()
    rewards: tuple[Reward, ...] = ()
    winner_user_id: int | None = None
    confirmation_expires_at: datetime | None = None


@dataclass(frozen=True)
class InterceptResult:
    status: InterceptStatus
    event_id: str
    reward: DropReward | None = None
    winner_user_id: int | None = None


@dataclass(frozen=True)
class CooperativeResult:
    status: CooperativeStatus
    event_id: str
    contributions: int = 0
    required_contributions: int = 0
    participant_user_ids: tuple[int, ...] = ()
    reward: Reward | None = None


@dataclass(frozen=True)
class ChaseResult:
    status: ChaseStatus
    event_id: str
    starter_user_id: int | None = None
    interceptor_user_id: int | None = None
    starter_reward: Reward | None = None
    interceptor_reward: Reward | None = None


@dataclass(frozen=True)
class NpcResult:
    status: NpcStatus
    event_id: str
    recipe_id: str
    reward: DropReward | None = None
    required_agents: tuple[AgentCost, ...] = ()
    required_items: tuple[ItemCost, ...] = ()


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
class AgencyResult:
    status: AgencyStatus
    agency_level: int
    required_reputation: int
    required_agents: tuple[AgentCost, ...] = ()


@dataclass(frozen=True)
class AgentHolding:
    agent_type: str
    amount: int


@dataclass(frozen=True)
class ItemHolding:
    item_type: str
    amount: int


@dataclass(frozen=True)
class EquippedItem:
    slot: int
    item_type: str


@dataclass(frozen=True)
class Inventory:
    items: tuple[ItemHolding, ...] = ()
    equipped: tuple[EquippedItem, ...] = ()
    slot_count: int = 0


@dataclass(frozen=True)
class EquipmentResult:
    status: EquipmentStatus
    item_type: str | None = None
    slot: int | None = None


@dataclass(frozen=True)
class Profile:
    user_id: int
    username: str | None
    display_name: str | None
    reputation: int
    agency_level: int
    total_agents: int


@dataclass(frozen=True)
class LeaderboardEntry:
    rank: int
    user_id: int
    display_name: str
    total_agents: int
    rare_agents: int
    reputation: int
    agency_level: int


@dataclass(frozen=True)
class ChatStatus:
    chat_id: int
    enabled: bool
    activity_score: float
    next_event_at: datetime | None
    active_event_id: str | None
    active_event_expires_at: datetime | None
    story_arc: str | None = None
    story_stage: int = 0
    story_summary: str | None = None


@dataclass(frozen=True)
class AdminResult:
    ok: bool
    message: str
    event: SpawnEvent | None = None
    message_id_to_close: int | None = None
