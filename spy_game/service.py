"""Spy Clicker composition root and scheduler lifecycle.

Use-case components share one runtime and SQLite executor. The service keeps the
established adapter-facing instance API; use_cases/ owns scenario validation and
transaction boundaries, persistence/ owns SQL operations.
"""
from __future__ import annotations

import random
from datetime import datetime
from .activity import ActivityTracker
from .database import SQLiteDatabase
from .director import GameDirector, build_director
from .duels import SpyDuelRepository
from .models import AdminResult, ChatStatus, TickResult
from .repositories import SpyRepository
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, ActivityTriggerSettings, RandomSource
from .settings import ACTIVITY_PROFILES, SpySettings
from .use_cases.base import UseCaseContext, UseCases, utc_now
from .use_cases.missions import MissionsUseCases
from .use_cases.html5 import Html5UseCases
from .use_cases.duels import DuelsUseCases
from .use_cases.events import EventsUseCases
from .use_cases.economy import EconomyUseCases
from .use_cases.achievements import AchievementsUseCases
from .persistence.achievements import AchievementsRepository


class SpyGameService(UseCases):
    def __init__(
        self,
        settings: SpySettings,
        *,
        rng: RandomSource | None = None,
        director: GameDirector | None = None,
    ) -> None:
        random_source = rng or random.SystemRandom()
        super().__init__(
            UseCaseContext(
                settings=settings,
                database=SQLiteDatabase(settings.database_path),
                repository=SpyRepository(
                    settings,
                    ActivityPolicy(settings),
                    random_source,
                    RewardResolver(settings),
                ),
                duel_repository=SpyDuelRepository(settings),
                rng=random_source,
            )
        )
        self.activity = ActivityTracker(settings.activity_user_debounce_seconds)
        self.director = director or build_director(settings, self.rng)
        self._startup_expired = ()
        self.missions = MissionsUseCases(self.context)
        self.html5 = Html5UseCases(self.context, missions=self.missions)
        self.duels = DuelsUseCases(self.context)
        self.events = EventsUseCases(self.context)
        self.economy = EconomyUseCases(self.context)
        self.achievement_repository = AchievementsRepository(settings)
        self.achievements = AchievementsUseCases(
            self.context, self.achievement_repository
        )
        self.database.before_commit = self.achievement_repository.drain
        self.get_achievements = self.achievements.get_achievements
        self.select_achievement_title = self.achievements.select_title
        self.mark_achievements_seen = self.achievements.mark_seen

        # Preserve instance entry points consumed by Telegram and HTTP adapters.
        self.start_death_mission = self.missions.start_death_mission
        self.get_death_mission = self.missions.get_death_mission
        self.mutate_death_mission = self.missions.mutate_death_mission
        self.mission_callback = self.missions.mission_callback
        self.death_mission_run_id = self.missions.death_mission_run_id
        self.reserved_mission_agents = self.missions.reserved_mission_agents
        self.start_intercept_game = self.html5.start_intercept_game
        self.start_html5_game = self.html5.start_html5_game
        self.start_dead_drop_game = self.html5.start_dead_drop_game
        self.get_dead_drop_game = self.html5.get_dead_drop_game
        self.guess_dead_drop_game = self.html5.guess_dead_drop_game
        self.get_intercept_game = self.html5.get_intercept_game
        self.start_find_mole_game = self.html5.start_find_mole_game
        self.get_find_mole_game = self.html5.get_find_mole_game
        self.get_html5_game = self.html5.get_html5_game
        self.accuse_find_mole_game = self.html5.accuse_find_mole_game
        self.accuse_find_mole_event = self.html5.accuse_find_mole_event
        self.finish_intercept_game = self.html5.finish_intercept_game
        self.create_duel_wager = self.duels.create_duel_wager
        self.attach_duel_message = self.duels.attach_duel_message
        self.get_duel_wager = self.duels.get_duel_wager
        self.get_active_duel_wager = self.duels.get_active_duel_wager
        self.accept_duel_wager = self.duels.accept_duel_wager
        self.choose_duel_move = self.duels.choose_duel_move
        self.forfeit_duel_wager = self.duels.forfeit_duel_wager
        self.close_pending_duel_wager = self.duels.close_pending_duel_wager
        self.cancel_duel_wager_as_master = self.duels.cancel_duel_wager_as_master
        self.expire_duel_wager = self.duels.expire_duel_wager
        self.claim_event = self.events.claim_event
        self.get_recruitment_progress = self.events.get_recruitment_progress
        self.exchange_with_handler = self.events.exchange_with_handler
        self.search_dead_drop = self.events.search_dead_drop
        self.run_death_operation = self.events.run_death_operation
        self.answer_intercept = self.events.answer_intercept
        self.contribute_cooperative = self.events.contribute_cooperative
        self.advance_chase = self.events.advance_chase
        self.interact_with_npc = self.events.interact_with_npc
        self.exchange_with_contact = self.economy.exchange_with_contact
        self.increase_reputation = self.economy.increase_reputation
        self.found_agency = self.economy.found_agency
        self.get_profile = self.economy.get_profile
        self.get_agents = self.economy.get_agents
        self.get_inventory = self.economy.get_inventory
        self.get_leaderboard = self.economy.get_leaderboard
        self.equip_item = self.economy.equip_item
        self.unequip_item = self.economy.unequip_item

    async def initialize(self, *, now: datetime | None = None) -> None:
        await self.database.initialize()
        current = now or utc_now()
        await self.database.transaction(
            lambda connection: self.repository.death_mission.reconcile(
                connection, current
            ),
            immediate=True,
        )
        self._startup_expired = await self.database.transaction(
            lambda connection: self.repository.lifecycle.reconcile(connection, current),
            immediate=True,
        )
        await self.database.transaction(
            lambda connection: self.duel_repository.reconcile(connection, current),
            immediate=True,
        )

    async def close(self) -> None:
        await self.database.close()

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
        current = now or utc_now()
        await self.database.transaction(
            lambda connection: self.duel_repository.reconcile(connection, current),
            immediate=True,
        )
        await self.database.transaction(
            lambda connection: self.repository.death_mission.reconcile(
                connection, current
            ),
            immediate=True,
        )
        if not self.settings.enabled:
            return TickResult()
        counts = await self.activity.drain()
        try:
            prepared = await self.database.transaction(
                lambda connection: self.repository.scheduling.prepare_tick(
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
                        self.repository.scheduling.spawn_due(
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
            lambda connection: self.repository.lifecycle.enable_chat(
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
            lambda connection: self.repository.lifecycle.disable_chat(
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
            lambda connection: self.repository.scheduling.set_activity_profile(
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
            "find_mole",
        }:
            return AdminResult(False, "Неизвестный тип события.")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.scheduling.manual_spawn(
                connection,
                chat_id,
                current,
                event_type,
            ),
            immediate=True,
        )

    async def attach_message(self, event_id: str, message_id: int) -> bool:
        return await self.database.transaction(
            lambda connection: self.repository.lifecycle.attach_message(
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
            lambda connection: self.repository.lifecycle.cancel_publication(
                connection, event_id, current
            ),
            immediate=True,
        )

    async def get_chat_status(self, chat_id: int) -> ChatStatus:
        return await self.database.read(
            lambda connection: self.repository.lifecycle.get_chat_status(
                connection, chat_id
            )
        )
