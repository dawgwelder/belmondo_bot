"""Spy Clicker duels use cases and transaction boundaries."""
from __future__ import annotations

from datetime import datetime
from ..duels import DUEL_ACTIONS
from ..models import DuelWager, DuelWagerStatus
from .base import UseCases, utc_now


class DuelsUseCases(UseCases):
    async def create_duel_wager(
        self,
        *,
        duel_id: str,
        chat_id: int,
        challenger_user_id: int,
        challenger_username: str | None,
        challenger_display_name: str | None,
        opponent_user_id: int | None,
        opponent_username: str | None,
        opponent_display_name: str | None,
        stake_amount: int,
        scenario: dict,
        now: datetime | None = None,
    ) -> DuelWager:
        if not self.chat_is_available(chat_id):
            return DuelWager(DuelWagerStatus.DISABLED, duel_id, chat_id=chat_id)
        if stake_amount not in self.settings.duel_stake_amounts:
            raise ValueError("invalid duel stake")
        if not isinstance(scenario, dict):
            raise ValueError("invalid duel scenario")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.create(
                connection,
                duel_id=duel_id,
                chat_id=chat_id,
                challenger_user_id=challenger_user_id,
                challenger_username=challenger_username,
                challenger_display_name=challenger_display_name,
                opponent_user_id=opponent_user_id,
                opponent_username=opponent_username,
                opponent_display_name=opponent_display_name,
                stake_amount=stake_amount,
                scenario=scenario,
                tie_breaker_role=(
                    "challenger" if self.rng.randint(0, 1) == 0 else "opponent"
                ),
                now=current,
            ),
            immediate=True,
        )

    async def attach_duel_message(
        self,
        duel_id: str,
        message_id: int,
    ) -> DuelWager:
        return await self.database.transaction(
            lambda connection: self.duel_repository.attach_message(
                connection,
                duel_id,
                message_id,
            ),
            immediate=True,
        )

    async def get_duel_wager(
        self,
        duel_id: str,
        *,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.get(
                connection,
                duel_id,
                current,
            ),
            immediate=True,
        )

    async def get_active_duel_wager(
        self,
        chat_id: int,
        *,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.get_active_for_chat(
                connection,
                chat_id,
                current,
            ),
            immediate=True,
        )

    async def accept_duel_wager(
        self,
        *,
        duel_id: str,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.accept(
                connection,
                duel_id=duel_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                now=current,
            ),
            immediate=True,
        )

    async def choose_duel_move(
        self,
        *,
        duel_id: str,
        user_id: int,
        action: str,
        now: datetime | None = None,
    ) -> DuelWager:
        if action not in DUEL_ACTIONS:
            raise ValueError("invalid duel action")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.choose(
                connection,
                duel_id=duel_id,
                user_id=user_id,
                action=action,
                now=current,
            ),
            immediate=True,
        )

    async def forfeit_duel_wager(
        self,
        duel_id: str,
        user_id: int,
        *,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.forfeit(
                connection,
                duel_id,
                user_id,
                current,
            ),
            immediate=True,
        )

    async def close_pending_duel_wager(
        self,
        *,
        duel_id: str,
        user_id: int,
        username: str | None,
        action: str,
        now: datetime | None = None,
    ) -> DuelWager:
        if action not in {"cancel", "decline"}:
            raise ValueError("invalid pending duel action")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.close_pending(
                connection,
                duel_id=duel_id,
                user_id=user_id,
                username=username,
                action=action,
                now=current,
            ),
            immediate=True,
        )

    async def cancel_duel_wager_as_master(
        self,
        duel_id: str,
        *,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.master_cancel(
                connection,
                duel_id,
                current,
            ),
            immediate=True,
        )

    async def expire_duel_wager(
        self,
        duel_id: str,
        *,
        now: datetime | None = None,
    ) -> DuelWager:
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.duel_repository.expire(
                connection,
                duel_id,
                current,
            ),
            immediate=True,
        )
