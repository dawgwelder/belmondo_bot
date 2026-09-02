"""Application service for persistent Spy Clicker use cases."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .activity import ActivityTracker
from .database import SQLiteDatabase
from .director import GameDirector, build_director
from .models import (
    AdminResult,
    AgencyResult,
    AgencyStatus,
    AgentHolding,
    ChatStatus,
    ChaseResult,
    ChaseStatus,
    ClaimResult,
    ClaimStatus,
    CooperativeResult,
    CooperativeStatus,
    DeadDropResult,
    DeathOperationResult,
    DeathOperationStatus,
    EconomyStatus,
    EquipmentResult,
    EquipmentStatus,
    ExchangeResult,
    Inventory,
    InterceptResult,
    InterceptStatus,
    LeaderboardEntry,
    NpcResult,
    NpcStatus,
    PrestigeResult,
    Profile,
    RecruitmentProgress,
    TickResult,
)
from .repositories import SpyRepository
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, ActivityTriggerSettings, RandomSource
from .settings import ACTIVITY_PROFILES, SpySettings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SpyGameService:
    def __init__(
        self,
        settings: SpySettings,
        *,
        rng: RandomSource | None = None,
        director: GameDirector | None = None,
    ) -> None:
        self.settings = settings
        self.database = SQLiteDatabase(settings.database_path)
        self.activity = ActivityTracker(settings.activity_user_debounce_seconds)
        self.rng = rng or random.SystemRandom()
        self.director = director or build_director(settings, self.rng)
        self.repository = SpyRepository(
            settings,
            ActivityPolicy(settings),
            self.rng,
            RewardResolver(settings),
        )
        self._startup_expired = ()

    async def initialize(self, *, now: datetime | None = None) -> None:
        await self.database.initialize()
        current = now or utc_now()
        self._startup_expired = await self.database.transaction(
            lambda connection: self.repository.reconcile(connection, current),
            immediate=True,
        )

    async def close(self) -> None:
        await self.database.close()

    def chat_is_available(self, chat_id: int) -> bool:
        return self.settings.enabled and self.settings.chat_is_allowed(chat_id)

    async def record_activity(
        self,
        chat_id: int,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        if not self.chat_is_available(chat_id):
            return False
        return await self.activity.record(chat_id, user_id, now or utc_now())

    async def tick(self, *, now: datetime | None = None) -> TickResult:
        if not self.settings.enabled:
            return TickResult()
        current = now or utc_now()
        counts = await self.activity.drain()
        try:
            prepared = await self.database.transaction(
                lambda connection: self.repository.prepare_tick(
                    connection,
                    counts,
                    self.settings.allowed_chat_ids,
                    current,
                ),
                immediate=True,
            )
            spawned = []
            for state in prepared.due:
                decision = await self.director.choose_event(state)
                event = await self.database.transaction(
                    lambda connection, state=state, decision=decision: (
                        self.repository.spawn_due(
                            connection,
                            state,
                            current,
                            decision,
                        )
                    ),
                    immediate=True,
                )
                if event is not None:
                    spawned.append(event)
            result = TickResult(tuple(spawned), prepared.expired)
            if self._startup_expired:
                result = TickResult(
                    spawned=result.spawned,
                    expired=tuple(self._startup_expired) + result.expired,
                )
                self._startup_expired = ()
            return result
        except Exception:
            await self.activity.restore(counts)
            raise

    async def enable_chat(
        self,
        chat_id: int,
        *,
        now: datetime | None = None,
    ) -> AdminResult:
        if not self.settings.enabled:
            return AdminResult(False, "Глобальный SPY_GAME_ENABLED выключен.")
        if not self.settings.chat_is_allowed(chat_id):
            return AdminResult(False, "Этот чат отсутствует в beta allowlist.")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.enable_chat(
                connection, chat_id, current
            ),
            immediate=True,
        )

    async def disable_chat(
        self,
        chat_id: int,
        *,
        now: datetime | None = None,
    ) -> AdminResult:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.disable_chat(
                connection, chat_id, current
            ),
            immediate=True,
        )

    def activity_trigger_settings(
        self,
        profile: str | None = None,
    ) -> ActivityTriggerSettings:
        return self.repository.activity_policy.trigger_settings(profile)

    async def set_activity_profile(
        self,
        chat_id: int,
        profile: str,
        *,
        now: datetime | None = None,
    ) -> AdminResult:
        if not self.settings.enabled:
            return AdminResult(False, "Глобальный SPY_GAME_ENABLED выключен.")
        if not self.settings.chat_is_allowed(chat_id):
            return AdminResult(False, "Этот чат отсутствует в beta allowlist.")
        if profile not in ACTIVITY_PROFILES:
            return AdminResult(
                False,
                "Неизвестный профиль. Доступны: calm, balanced, aggressive.",
            )
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.set_activity_profile(
                connection,
                chat_id,
                profile,
                current,
            ),
            immediate=True,
        )

    async def manual_spawn(
        self,
        chat_id: int,
        *,
        event_type: str = "recruitment",
        now: datetime | None = None,
    ) -> AdminResult:
        if not self.chat_is_available(chat_id):
            return AdminResult(False, "Игра недоступна в этом чате.")
        if not self.settings.allow_manual_spawn:
            return AdminResult(False, "Ручной spawn запрещён конфигурацией.")
        if event_type not in {
            "recruitment",
            "dead_drop",
            "handler",
            "death_operation",
            "intercept",
            "cooperative_operation",
            "chase",
            "npc",
        }:
            return AdminResult(False, "Неизвестный тип события.")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.manual_spawn(
                connection,
                chat_id,
                current,
                event_type,
            ),
            immediate=True,
        )

    async def attach_message(self, event_id: str, message_id: int) -> bool:
        return await self.database.transaction(
            lambda connection: self.repository.attach_message(
                connection, event_id, message_id
            ),
            immediate=True,
        )

    async def cancel_publication(
        self,
        event_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.cancel_publication(
                connection, event_id, current
            ),
            immediate=True,
        )

    async def claim_event(
        self,
        *,
        event_id: str,
        action: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> ClaimResult:
        if action != "claim":
            return ClaimResult(ClaimStatus.INVALID_ACTION, event_id)
        if not self.chat_is_available(chat_id):
            return ClaimResult(ClaimStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.claim(
                connection,
                event_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def get_recruitment_progress(
        self,
        event_id: str,
    ) -> RecruitmentProgress | None:
        return await self.database.read(
            lambda connection: self.repository.get_recruitment_progress(
                connection,
                event_id,
            )
        )

    async def exchange_with_handler(
        self,
        *,
        event_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> ExchangeResult:
        if not self.chat_is_available(chat_id):
            return ExchangeResult(
                EconomyStatus.DISABLED,
                event_id,
                recipe_id,
            )
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.exchange_with_handler(
                connection,
                event_id,
                recipe_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def search_dead_drop(
        self,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> DeadDropResult:
        if not self.chat_is_available(chat_id):
            return DeadDropResult(ClaimStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.claim_dead_drop(
                connection,
                event_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def run_death_operation(
        self,
        *,
        event_id: str,
        action: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> DeathOperationResult:
        if action != "death":
            return DeathOperationResult(
                DeathOperationStatus.INVALID_ACTION,
                event_id,
            )
        if not self.chat_is_available(chat_id):
            return DeathOperationResult(DeathOperationStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.run_death_operation(
                connection,
                event_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def answer_intercept(
        self,
        *,
        event_id: str,
        choice_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> InterceptResult:
        if not self.chat_is_available(chat_id):
            return InterceptResult(InterceptStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.answer_intercept(
                connection,
                event_id,
                choice_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def contribute_cooperative(
        self,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> CooperativeResult:
        if not self.chat_is_available(chat_id):
            return CooperativeResult(CooperativeStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.contribute_cooperative(
                connection,
                event_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def advance_chase(
        self,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> ChaseResult:
        if not self.chat_is_available(chat_id):
            return ChaseResult(ChaseStatus.DISABLED, event_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.advance_chase(
                connection,
                event_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def interact_with_npc(
        self,
        *,
        event_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> NpcResult:
        if not self.chat_is_available(chat_id):
            return NpcResult(NpcStatus.DISABLED, event_id, recipe_id)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.interact_with_npc(
                connection,
                event_id,
                recipe_id,
                chat_id,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def increase_reputation(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_reputation: int,
        now: datetime | None = None,
    ) -> PrestigeResult:
        if not self.chat_is_available(chat_id):
            return PrestigeResult(EconomyStatus.DISABLED, expected_reputation)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.increase_reputation(
                connection,
                chat_id,
                user_id,
                username,
                display_name,
                expected_reputation,
                current,
            ),
            immediate=True,
        )

    async def found_agency(
        self,
        *,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_agency_level: int,
        now: datetime | None = None,
    ) -> AgencyResult:
        required_reputation = self.settings.agency_reputation_requirement(
            expected_agency_level
        )
        required_agents = self.settings.agency_requirements(expected_agency_level)
        if not self.chat_is_available(chat_id):
            return AgencyResult(
                AgencyStatus.DISABLED,
                expected_agency_level,
                required_reputation,
                required_agents,
            )
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.found_agency(
                connection,
                chat_id,
                user_id,
                username,
                display_name,
                expected_agency_level,
                current,
            ),
            immediate=True,
        )

    async def get_profile(
        self,
        *,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> Profile:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.ensure_user_and_profile(
                connection,
                user_id,
                username,
                display_name,
                current,
            ),
            immediate=True,
        )

    async def get_agents(self, user_id: int) -> tuple[AgentHolding, ...]:
        return await self.database.read(
            lambda connection: self.repository.get_agents(connection, user_id)
        )

    async def get_inventory(self, user_id: int) -> Inventory:
        return await self.database.read(
            lambda connection: self.repository.get_inventory(connection, user_id)
        )

    async def get_leaderboard(self, limit: int = 10) -> tuple[LeaderboardEntry, ...]:
        bounded_limit = max(1, min(limit, 25))
        return await self.database.read(
            lambda connection: self.repository.get_leaderboard(
                connection,
                bounded_limit,
            )
        )

    async def equip_item(
        self,
        *,
        chat_id: int,
        user_id: int,
        item_type: str,
    ) -> EquipmentResult:
        if not self.chat_is_available(chat_id):
            return EquipmentResult(EquipmentStatus.DISABLED, item_type)
        return await self.database.transaction(
            lambda connection: self.repository.equip_item(
                connection,
                chat_id,
                user_id,
                item_type,
            ),
            immediate=True,
        )

    async def unequip_item(
        self,
        *,
        chat_id: int,
        user_id: int,
        slot: int,
    ) -> EquipmentResult:
        if not self.chat_is_available(chat_id):
            return EquipmentResult(EquipmentStatus.DISABLED, slot=slot)
        return await self.database.transaction(
            lambda connection: self.repository.unequip_item(
                connection,
                chat_id,
                user_id,
                slot,
            ),
            immediate=True,
        )

    async def get_chat_status(self, chat_id: int) -> ChatStatus:
        return await self.database.read(
            lambda connection: self.repository.get_chat_status(connection, chat_id)
        )
