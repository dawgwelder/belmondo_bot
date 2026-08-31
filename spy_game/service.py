"""Application service for persistent Spy Clicker use cases."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .activity import ActivityTracker
from .database import SQLiteDatabase
from .director import GameDirector, RuleBasedDirector
from .models import (
    AdminResult,
    AgentHolding,
    ChatStatus,
    ClaimResult,
    ClaimStatus,
    EconomyStatus,
    ExchangeResult,
    PrestigeResult,
    Profile,
    TickResult,
)
from .repositories import SpyRepository
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, RandomSource
from .settings import SpySettings


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
        self.director = director or RuleBasedDirector(settings, self.rng)
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
            result = await self.database.transaction(
                lambda connection: self.repository.run_tick(
                    connection,
                    counts,
                    self.settings.allowed_chat_ids,
                    current,
                    self.director,
                ),
                immediate=True,
            )
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
        if event_type not in {"recruitment", "handler"}:
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

    async def get_chat_status(self, chat_id: int) -> ChatStatus:
        return await self.database.read(
            lambda connection: self.repository.get_chat_status(connection, chat_id)
        )
