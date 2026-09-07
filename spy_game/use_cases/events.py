"""Spy Clicker events use cases and transaction boundaries."""
from __future__ import annotations

from datetime import datetime
from ..models import (
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
    ExchangeResult,
    InterceptResult,
    InterceptStatus,
    NpcResult,
    NpcStatus,
    RecruitmentProgress,
)
from .base import UseCases, utc_now


class EventsUseCases(UseCases):
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
            lambda connection: self.repository.recruitment.claim(
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
            lambda connection: self.repository.recruitment.get_recruitment_progress(
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
            lambda connection: self.repository.contacts.exchange_with_handler(
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
            lambda connection: self.repository.dead_drop.claim_dead_drop(
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
            lambda connection: self.repository.death_operation.run_death_operation(
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
            lambda connection: self.repository.intercept.answer_intercept(
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
            lambda connection: self.repository.cooperative.contribute_cooperative(
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
            lambda connection: self.repository.chase.advance_chase(
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
            lambda connection: self.repository.contacts.interact_with_npc(
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
