"""Typed runtime and balance settings for Spy Clicker."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    AgentCost,
    AgentType,
    DropEntry,
    EventWeight,
    ExchangeRecipe,
    InterceptOption,
    InterceptScenario,
    ItemCost,
    ItemCategory,
    ItemType,
    MoleCaseTemplate,
    MoleSuspect,
    NpcRecipe,
)


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
        AgentType("resident", 4, "Резидент", "🏛"),
        AgentType("illegal_agent", 4, "Агент-нелегал", "🪪"),
        AgentType("ghost_agent", 5, "Агент-призрак", "👻"),
        AgentType("intelligence_director", 6, "Директор разведки", "♟"),
    )
}

ITEM_TYPES: dict[str, ItemType] = {
    item.id: item
    for item in (
        ItemType("fake_passport", "Поддельный паспорт", "🛂", ItemCategory.EQUIPMENT),
        ItemType("radio", "Полевая рация", "📻", ItemCategory.EQUIPMENT),
        ItemType("wiretap", "Комплект прослушки", "🎙", ItemCategory.EQUIPMENT),
        ItemType("intel_file", "Разведданные", "📁", ItemCategory.CONSUMABLE),
        ItemType("satellite_image", "Спутниковый снимок", "🛰", ItemCategory.CONSUMABLE),
        ItemType("access_code", "Код доступа", "🔐", ItemCategory.CONSUMABLE),
    )
}

DEFAULT_EVENT_WEIGHTS = (
    EventWeight("recruitment", 8),
    EventWeight("dead_drop", 3),
    EventWeight("intercept", 3),
    EventWeight("cooperative_operation", 2),
    EventWeight("chase", 2),
    EventWeight("handler", 2),
    EventWeight("npc", 1),
    EventWeight("death_operation", 1),
    EventWeight("find_mole", 1),
)

ACTIVITY_PROFILES = ("calm", "balanced", "aggressive")

DEFAULT_INTERCEPT_SCENARIOS = (
    InterceptScenario(
        id="midnight_frequency",
        prompt="Передача назначена на полночь. Какой канал назвал связной?",
        options=(
            InterceptOption("alpha", "Канал Альфа"),
            InterceptOption("bravo", "Канал Браво"),
            InterceptOption("charlie", "Канал Чарли"),
        ),
        correct_option_id="bravo",
        reward_item="access_code",
    ),
    InterceptScenario(
        id="station_cipher",
        prompt="В шифровке повторяется фраза «последний поезд». Где точка встречи?",
        options=(
            InterceptOption("north", "Северный вокзал"),
            InterceptOption("museum", "Старый музей"),
            InterceptOption("harbor", "Речной порт"),
        ),
        correct_option_id="north",
        reward_item="intel_file",
    ),
)

DEFAULT_MOLE_CASES = (
    MoleCaseTemplate(
        id="archive_leak",
        version=1,
        title="Утечка из архива Секции 7",
        briefing=(
            "Крот вынес копию личного дела Вяземского. Сопоставьте маршрут, "
            "служебный канал и след на архивной плёнке."
        ),
        clues=(
            "Копию сняли на плёнку из восточного архивного терминала.",
            "Крот вышел через речной КПП и передал пакет по каналу «Янтарь».",
            "Журнал доступа диспетчерской подтверждён: его запись не подделывали.",
            "На плёнке осталась красная пыль из ремонтного тоннеля.",
        ),
        suspects=(
            MoleSuspect(
                "orion",
                "Орион",
                "архивист",
                "Работал у западного терминала; канал «Кобальт», выход через метро.",
                ("west_terminal", "cobalt", "metro"),
            ),
            MoleSuspect(
                "lark",
                "Жаворонок",
                "курьер",
                "Был у восточного терминала и шёл через речной КПП; "
                "канал «Янтарь», одежда в красной пыли.",
                ("east_terminal", "amber", "river", "red_dust"),
            ),
            MoleSuspect(
                "sable",
                "Соболь",
                "аналитик",
                "Оставался в диспетчерской; канал «Янтарь», выход через метро.",
                ("control_room", "amber", "metro", "verified_log"),
            ),
            MoleSuspect(
                "atlas",
                "Атлас",
                "техник связи",
                "Осматривал восточный терминал; канал «Фиолет», выход через северный КПП.",
                ("east_terminal", "violet", "north", "red_dust"),
            ),
        ),
        solution_tags=("east_terminal", "amber", "river", "red_dust"),
    ),
    MoleCaseTemplate(
        id="embassy_signal",
        version=1,
        title="Сигнал у старого посольства",
        briefing=(
            "Перед исчезновением полковник оставил четыре перекрёстные отметки. "
            "Только одно досье совпадает со всеми следами."
        ),
        clues=(
            "Передатчик включили с крыши старого посольства.",
            "В эфир ушёл позывной «Маяк».",
            "Курьер унёс пакет на северный вокзал.",
            "На конверте найдено масло от типографского пресса.",
        ),
        suspects=(
            MoleSuspect(
                "rook",
                "Ладья",
                "радист",
                "Был на крыше посольства; позывной «Маяк», маршрут к порту.",
                ("embassy_roof", "beacon", "harbor"),
            ),
            MoleSuspect(
                "mistral",
                "Мистраль",
                "печатник",
                "С крыши посольства нёс пакет на северный вокзал; "
                "позывной «Маяк», след типографского масла.",
                ("embassy_roof", "beacon", "north_station", "press_oil"),
            ),
            MoleSuspect(
                "cedar",
                "Кедр",
                "дипкурьер",
                "Нёс пакет к посольству; позывной «Факел», маршрут к северному вокзалу.",
                ("embassy_lobby", "torch", "north_station"),
            ),
            MoleSuspect(
                "vega",
                "Вега",
                "наблюдатель",
                "Дежурил напротив посольства; позывной «Маяк», след оружейного масла.",
                ("embassy_street", "beacon", "gun_oil"),
            ),
        ),
        solution_tags=("embassy_roof", "beacon", "north_station", "press_oil"),
    ),
)

DEFAULT_DEAD_DROP_ENTRIES = (
    DropEntry("item", "intel_file", 1, 28),
    DropEntry("item", "fake_passport", 1, 12),
    DropEntry("item", "radio", 1, 10),
    DropEntry("item", "wiretap", 1, 8),
    DropEntry("item", "satellite_image", 1, 7),
    DropEntry("item", "access_code", 1, 5),
    DropEntry("agent", "informant", 2, 20),
    DropEntry("empty", None, 0, 10),
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

DEFAULT_NPC_RECIPES = (
    NpcRecipe(
        id="recruiter_network",
        npc_id="recruiter",
        display_name="Подбор агента · 20 осведомителей",
        agent_costs=(AgentCost("informant", 20),),
        item_costs=(),
        rewards=(
            DropEntry("agent", "operative", 1, 24),
            DropEntry("agent", "observer", 1, 23),
            DropEntry("agent", "courier", 1, 23),
            DropEntry("agent", "analyst", 1, 7),
            DropEntry("agent", "saboteur", 1, 6),
            DropEntry("agent", "sleeper", 1, 6),
            DropEntry("agent", "double_agent", 1, 6),
            DropEntry("agent", "resident", 1, 3),
            DropEntry("agent", "illegal_agent", 1, 2),
        ),
    ),
    NpcRecipe(
        id="chief_illegal",
        npc_id="operations_chief",
        display_name="Создать агента-нелегала",
        agent_costs=(AgentCost("operative", 1),),
        item_costs=(ItemCost("fake_passport", 1),),
        rewards=(DropEntry("agent", "illegal_agent", 1, 1),),
    ),
    NpcRecipe(
        id="chief_resident",
        npc_id="operations_chief",
        display_name="Подготовить резидента",
        agent_costs=(AgentCost("observer", 1),),
        item_costs=(ItemCost("satellite_image", 1),),
        rewards=(DropEntry("agent", "resident", 1, 1),),
    ),
    NpcRecipe(
        id="chief_director",
        npc_id="operations_chief",
        display_name="Назначить директора разведки",
        agent_costs=(
            AgentCost("analyst", 1),
            AgentCost("resident", 1),
            AgentCost("illegal_agent", 1),
        ),
        item_costs=(ItemCost("access_code", 1),),
        rewards=(DropEntry("agent", "intelligence_director", 1, 1),),
    ),
    NpcRecipe(
        id="counter_double",
        npc_id="counterintelligence",
        display_name="Проверить оперативника",
        agent_costs=(AgentCost("operative", 1),),
        item_costs=(ItemCost("intel_file", 1),),
        rewards=(DropEntry("agent", "double_agent", 1, 1),),
    ),
    NpcRecipe(
        id="counter_ghost",
        npc_id="counterintelligence",
        display_name="Стереть след спящего агента",
        agent_costs=(AgentCost("sleeper", 1),),
        item_costs=(ItemCost("access_code", 1),),
        rewards=(DropEntry("agent", "ghost_agent", 1, 1),),
    ),
    NpcRecipe(
        id="counter_cache",
        npc_id="counterintelligence",
        display_name="Проверить сеть информаторов",
        agent_costs=(AgentCost("informant", 5),),
        item_costs=(ItemCost("intel_file", 1),),
        rewards=(DropEntry("item", "satellite_image", 1, 1),),
    ),
)

PERMANENT_CONTACT_NPC_IDS = frozenset(
    {
        "recruiter",
        "operations_chief",
        "counterintelligence",
    }
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
    activity_threshold: float = 5.5
    activity_peak_messages: int = 3
    activity_inertia_window_seconds: int = 2 * 60
    activity_inertia_one_in: int = 2
    activity_random_average_seconds: int = 90 * 60
    activity_event_cooldown_seconds: int = 10 * 60
    activity_message_points: float = 1.0
    activity_user_debounce_seconds: int = 20
    activity_after_spawn_ratio: float = 0.45
    max_activity_score: float = 100.0
    default_activity_profile: str = "balanced"
    allow_manual_spawn: bool = False
    llm_narrator_enabled: bool = False
    llm_narrator_timeout_seconds: int = 8
    llm_director_enabled: bool = False
    llm_director_timeout_seconds: int = 8
    recruitment_agent_type: str = "informant"
    recruitment_winner_count: int = 3
    reputation_reward_cap: int = 5
    equipment_slots: int = 3
    wiretap_bonus_chance_percent: int = 20
    intercept_game_rounds: int = 5
    intercept_game_run_seconds: int = 45
    intercept_game_success_score: int = 3000
    dead_drop_game_code_length: int = 3
    dead_drop_game_run_seconds: int = 5 * 60
    html5_mole_enabled: bool = False
    mole_game_run_seconds: int = 5 * 60
    mole_suspect_count: int = 4
    mole_reward_item_amount: int = 1
    mole_reward_agent_type: str = "informant"
    mole_reward_agent_amount: int = 3
    duel_stake_agent_type: str = "informant"
    duel_stake_amounts: tuple[int, ...] = (1, 3, 5)
    duel_accept_seconds: int = 120
    duel_move_seconds: int = 180
    death_mission_enabled: bool = False
    death_mission_seconds: int = 15 * 60
    death_mission_tier4_pool: tuple[str, ...] = ("resident", "illegal_agent")
    death_operation_success_percent: int = 35
    death_operation_confirmation_seconds: int = 60
    death_operation_reward_multiplier: int = 2
    death_operation_bonus_pool: tuple[str, ...] = (
        "analyst",
        "saboteur",
        "sleeper",
        "double_agent",
    )
    cooperative_required_contributions: int = 3
    cooperative_reward_agent: str = "informant"
    cooperative_reward_amount: int = 2
    chase_starter_reward: AgentCost = AgentCost("informant", 1)
    chase_interceptor_reward: AgentCost = AgentCost("operative", 1)
    agency_max_level: int = 5
    agency_required_reputation: int = 3
    agency_required_directors: int = 1
    agency_required_residents: int = 2
    agency_required_illegal_agents: int = 2
    agency_rare_bonus_percent: int = 5
    handler_event_reward_multiplier: int = 2
    npc_event_reward_multiplier: int = 2
    event_weights: tuple[EventWeight, ...] = field(
        default_factory=lambda: DEFAULT_EVENT_WEIGHTS
    )
    handler_recipes: tuple[ExchangeRecipe, ...] = field(
        default_factory=lambda: DEFAULT_HANDLER_RECIPES
    )
    npc_recipes: tuple[NpcRecipe, ...] = field(
        default_factory=lambda: DEFAULT_NPC_RECIPES
    )
    prestige_base_costs: tuple[AgentCost, ...] = field(
        default_factory=lambda: DEFAULT_PRESTIGE_COSTS
    )
    dead_drop_entries: tuple[DropEntry, ...] = field(
        default_factory=lambda: DEFAULT_DEAD_DROP_ENTRIES
    )
    intercept_scenarios: tuple[InterceptScenario, ...] = field(
        default_factory=lambda: DEFAULT_INTERCEPT_SCENARIOS
    )
    mole_cases: tuple[MoleCaseTemplate, ...] = field(
        default_factory=lambda: DEFAULT_MOLE_CASES
    )
    mole_reward_item_pool: tuple[str, ...] = field(
        default_factory=lambda: tuple(ITEM_TYPES)
    )

    def __post_init__(self) -> None:
        if self.mode not in {"dev", "prod"}:
            raise ValueError("mode must be dev or prod")
        if self.tick_seconds <= 0 or self.event_lifetime_seconds <= 0:
            raise ValueError("tick and event lifetime must be positive")
        if self.activity_half_life_seconds <= 0 or self.activity_threshold <= 0:
            raise ValueError("activity settings must be positive")
        if (
            self.activity_peak_messages <= 0
            or self.activity_inertia_window_seconds <= 0
            or self.activity_inertia_one_in <= 0
            or self.activity_random_average_seconds <= 0
            or self.activity_event_cooldown_seconds < 0
        ):
            raise ValueError("activity trigger settings are invalid")
        if (
            self.llm_narrator_timeout_seconds <= 0
            or self.llm_director_timeout_seconds <= 0
        ):
            raise ValueError("LLM timeouts must be positive")
        if not 0 <= self.activity_after_spawn_ratio < 1:
            raise ValueError("activity_after_spawn_ratio must be in [0, 1)")
        if self.default_activity_profile not in ACTIVITY_PROFILES:
            raise ValueError("unknown default activity profile")
        if self.recruitment_agent_type not in AGENT_TYPES:
            raise ValueError("unknown recruitment agent type")
        if self.recruitment_winner_count <= 0:
            raise ValueError("recruitment winner count must be positive")
        if self.enabled and not self.allowed_chat_ids:
            raise ValueError("enabled Spy Game requires a non-empty chat allowlist")
        event_types = {item.event_type for item in self.event_weights}
        if event_types != {
            "recruitment",
            "dead_drop",
            "handler",
            "death_operation",
            "intercept",
            "cooperative_operation",
            "chase",
            "npc",
            "find_mole",
        }:
            raise ValueError(
                "event weights must configure recruitment, dead_drop, handler "
                "and death_operation, intercept, cooperative_operation, chase, npc "
                "and find_mole"
            )
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
        if self.equipment_slots <= 0:
            raise ValueError("equipment slots must be positive")
        if not 0 <= self.wiretap_bonus_chance_percent <= 100:
            raise ValueError("wiretap bonus chance must be between 0 and 100")
        if self.intercept_game_rounds <= 0 or self.intercept_game_run_seconds <= 0:
            raise ValueError("intercept game rounds and duration must be positive")
        if (
            not 1
            <= self.intercept_game_success_score
            <= (self.intercept_game_rounds * 1000)
        ):
            raise ValueError("intercept game success score is out of range")
        if not 2 <= self.dead_drop_game_code_length <= 6:
            raise ValueError("dead drop game code length must be between 2 and 6")
        if self.dead_drop_game_run_seconds <= 0:
            raise ValueError("dead drop game duration must be positive")
        if self.mole_game_run_seconds <= 0 or self.mole_suspect_count != 4:
            raise ValueError("mole game requires four suspects and a positive duration")
        if (
            self.mole_reward_item_amount <= 0
            or not self.mole_reward_item_pool
            or any(item_id not in ITEM_TYPES for item_id in self.mole_reward_item_pool)
        ):
            raise ValueError("mole item reward pool is invalid")
        if (
            self.mole_reward_agent_type not in AGENT_TYPES
            or AGENT_TYPES[self.mole_reward_agent_type].tier != 1
            or self.mole_reward_agent_amount <= 0
        ):
            raise ValueError("mole agent reward must contain a Tier 1 agent")
        if self.duel_stake_agent_type not in AGENT_TYPES:
            raise ValueError("duel stake agent type is unknown")
        if (
            not self.duel_stake_amounts
            or any(amount <= 0 for amount in self.duel_stake_amounts)
            or len(self.duel_stake_amounts) != len(set(self.duel_stake_amounts))
        ):
            raise ValueError("duel stake amounts must be positive and unique")
        if self.duel_accept_seconds <= 0 or self.duel_move_seconds <= 0:
            raise ValueError("duel timeouts must be positive")
        if self.death_mission_seconds <= 0:
            raise ValueError("death mission duration must be positive")
        if not self.death_mission_tier4_pool or any(
            agent not in AGENT_TYPES or AGENT_TYPES[agent].tier != 4
            for agent in self.death_mission_tier4_pool
        ):
            raise ValueError("death mission bonus pool must contain Tier 4 agents")
        if not 0 <= self.death_operation_success_percent <= 100:
            raise ValueError("death operation success chance must be between 0 and 100")
        if self.death_operation_confirmation_seconds <= 0:
            raise ValueError("death operation confirmation window must be positive")
        if self.death_operation_reward_multiplier < 2:
            raise ValueError("death operation reward multiplier must be at least 2")
        if not self.death_operation_bonus_pool or any(
            agent_id not in AGENT_TYPES or AGENT_TYPES[agent_id].tier != 3
            for agent_id in self.death_operation_bonus_pool
        ):
            raise ValueError("death operation bonus pool must contain Tier 3 agents")
        if self.cooperative_required_contributions < 2:
            raise ValueError("cooperative operation requires at least two players")
        if self.cooperative_reward_agent not in AGENT_TYPES:
            raise ValueError("cooperative operation reward agent is unknown")
        if self.cooperative_reward_amount <= 0:
            raise ValueError("cooperative operation reward must be positive")
        for reward in (self.chase_starter_reward, self.chase_interceptor_reward):
            if reward.agent_type not in AGENT_TYPES or reward.amount <= 0:
                raise ValueError("chase rewards must contain known agents")
        if self.agency_max_level <= 0 or self.agency_required_reputation <= 0:
            raise ValueError("agency progression settings must be positive")
        if not 0 <= self.agency_rare_bonus_percent <= 20:
            raise ValueError("agency rare bonus must be between 0 and 20")
        if (
            self.handler_event_reward_multiplier < 2
            or self.npc_event_reward_multiplier < 2
        ):
            raise ValueError("contact event reward multipliers must be at least 2")
        if any(
            amount <= 0
            for amount in (
                self.agency_required_directors,
                self.agency_required_residents,
                self.agency_required_illegal_agents,
            )
        ):
            raise ValueError("agency requirements must be positive")
        recipe_ids = [recipe.id for recipe in self.npc_recipes]
        if not recipe_ids or len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("NPC recipe IDs must be non-empty and unique")
        if {recipe.npc_id for recipe in self.npc_recipes} != {
            "recruiter",
            "operations_chief",
            "counterintelligence",
        }:
            raise ValueError("NPC recipes must configure all NPC types")
        for recipe in self.npc_recipes:
            self._validate_costs(recipe.agent_costs)
            self._validate_item_costs(recipe.item_costs)
            if not recipe.rewards:
                raise ValueError("NPC reward pool must not be empty")
            for reward in recipe.rewards:
                self._validate_drop_entry(reward, "NPC")
                if reward.reward_type == "empty":
                    raise ValueError("NPC reward pool cannot contain empty rewards")
        scenario_ids = [scenario.id for scenario in self.intercept_scenarios]
        if not scenario_ids or len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("intercept scenario IDs must be non-empty and unique")
        for scenario in self.intercept_scenarios:
            option_ids = [option.id for option in scenario.options]
            if (
                len(option_ids) < 2
                or len(option_ids) != len(set(option_ids))
                or scenario.correct_option_id not in option_ids
                or scenario.reward_item not in ITEM_TYPES
                or scenario.reward_amount <= 0
            ):
                raise ValueError("intercept scenario is invalid")
        case_ids = [case.id for case in self.mole_cases]
        if not case_ids or len(case_ids) != len(set(case_ids)):
            raise ValueError("mole case IDs must be non-empty and unique")
        for case in self.mole_cases:
            suspect_ids = [suspect.id for suspect in case.suspects]
            if (
                case.version <= 0
                or len(suspect_ids) != self.mole_suspect_count
                or len(suspect_ids) != len(set(suspect_ids))
                or not case.clues
                or not case.solution_tags
                or len(case.solution_candidates) != 1
            ):
                raise ValueError("mole case must have exactly one valid solution")
        if not self.dead_drop_entries:
            raise ValueError("dead drop registry must not be empty")
        for entry in self.dead_drop_entries:
            self._validate_drop_entry(entry, "dead drop")

    @staticmethod
    def _validate_costs(costs: tuple[AgentCost, ...]) -> None:
        if not costs or any(cost.amount <= 0 for cost in costs):
            raise ValueError("agent costs must be non-empty and positive")
        ids = [cost.agent_type for cost in costs]
        if len(ids) != len(set(ids)) or any(item not in AGENT_TYPES for item in ids):
            raise ValueError("agent costs must contain unique known agent types")

    @staticmethod
    def _validate_item_costs(costs: tuple[ItemCost, ...]) -> None:
        if any(cost.amount <= 0 for cost in costs):
            raise ValueError("item costs must be positive")
        ids = [cost.item_type for cost in costs]
        if len(ids) != len(set(ids)) or any(item not in ITEM_TYPES for item in ids):
            raise ValueError("item costs must contain unique known item types")

    @staticmethod
    def _validate_drop_entry(entry: DropEntry, label: str) -> None:
        if entry.weight <= 0 or entry.amount < 0:
            raise ValueError(f"{label} weights and amounts are invalid")
        if entry.reward_type == "item" and entry.reward_id not in ITEM_TYPES:
            raise ValueError(f"{label} contains an unknown item")
        if entry.reward_type == "agent" and entry.reward_id not in AGENT_TYPES:
            raise ValueError(f"{label} contains an unknown agent")
        if entry.reward_type == "empty" and (
            entry.reward_id is not None or entry.amount != 0
        ):
            raise ValueError(f"empty {label} reward must not contain a value")
        if entry.reward_type not in {"item", "agent", "empty"}:
            raise ValueError(f"{label} contains an unknown reward type")

    def handler_recipe(self, recipe_id: str) -> ExchangeRecipe | None:
        return next(
            (recipe for recipe in self.handler_recipes if recipe.id == recipe_id),
            None,
        )

    def intercept_scenario(self, scenario_id: str) -> InterceptScenario | None:
        return next(
            (
                scenario
                for scenario in self.intercept_scenarios
                if scenario.id == scenario_id
            ),
            None,
        )

    def mole_case(self, case_id: str) -> MoleCaseTemplate | None:
        return next((case for case in self.mole_cases if case.id == case_id), None)

    def npc_recipe(self, recipe_id: str) -> NpcRecipe | None:
        return next(
            (recipe for recipe in self.npc_recipes if recipe.id == recipe_id),
            None,
        )

    def npc_recipes_for(self, npc_id: str) -> tuple[NpcRecipe, ...]:
        return tuple(recipe for recipe in self.npc_recipes if recipe.npc_id == npc_id)

    @property
    def handler_contact_recipes(self) -> tuple[NpcRecipe, ...]:
        return tuple(
            NpcRecipe(
                id=f"handler_{recipe.id}",
                npc_id="handler",
                display_name=recipe.display_name,
                agent_costs=recipe.costs,
                item_costs=(),
                rewards=tuple(
                    DropEntry("agent", agent_id, recipe.reward_amount, 1)
                    for agent_id in recipe.reward_pool
                ),
            )
            for recipe in self.handler_recipes
        )

    @property
    def permanent_contact_recipes(self) -> tuple[NpcRecipe, ...]:
        npc_contacts = tuple(
            recipe
            for recipe in self.npc_recipes
            if recipe.npc_id in PERMANENT_CONTACT_NPC_IDS
        )
        return self.handler_contact_recipes + npc_contacts

    def permanent_contact_recipe(self, recipe_id: str) -> NpcRecipe | None:
        return next(
            (
                recipe
                for recipe in self.permanent_contact_recipes
                if recipe.id == recipe_id
            ),
            None,
        )

    @staticmethod
    def agent_tier(agent_id: str | None) -> int:
        agent = AGENT_TYPES.get(agent_id or "")
        return agent.tier if agent else 0

    @property
    def npc_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(recipe.npc_id for recipe in self.npc_recipes))

    @property
    def event_npc_ids(self) -> tuple[str, ...]:
        return self.npc_ids

    def agency_requirements(self, agency_level: int) -> tuple[AgentCost, ...]:
        multiplier = agency_level + 1
        return (
            AgentCost("intelligence_director", self.agency_required_directors),
            AgentCost("resident", self.agency_required_residents * multiplier),
            AgentCost(
                "illegal_agent",
                self.agency_required_illegal_agents * multiplier,
            ),
        )

    def agency_reputation_requirement(self, agency_level: int) -> int:
        return self.agency_required_reputation + agency_level

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
            llm_director_enabled=_env_bool("SPY_GAME_LLM_DIRECTOR_ENABLED", False),
            llm_director_timeout_seconds=_env_int(
                "SPY_GAME_LLM_DIRECTOR_TIMEOUT_SECONDS", 8
            ),
            html5_mole_enabled=_env_bool("SPY_GAME_HTML5_MOLE_ENABLED", False),
            death_mission_enabled=_env_bool("SPY_GAME_DEATH_ROGUELITE_ENABLED", False),
            death_mission_seconds=_env_int("SPY_GAME_DEATH_MISSION_SECONDS", 900),
            mole_game_run_seconds=_env_int("SPY_GAME_MOLE_RUN_SECONDS", 5 * 60),
        )

    def chat_is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids
