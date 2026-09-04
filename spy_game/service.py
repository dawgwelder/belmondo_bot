"""Application service for persistent Spy Clicker use cases."""

from __future__ import annotations

import hashlib
import random
import re
import secrets
from dataclasses import replace
from datetime import datetime, timezone

from .activity import ActivityTracker
from .database import SQLiteDatabase
from .director import GameDirector, build_director
from .duels import DUEL_ACTIONS, SpyDuelRepository
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
    ContactExchangeResult,
    DeadDropGameRun,
    DeadDropGameStatus,
    DeadDropResult,
    DeathOperationResult,
    DeathOperationStatus,
    DuelWager,
    DuelWagerStatus,
    EconomyStatus,
    EquipmentResult,
    EquipmentStatus,
    ExchangeResult,
    Inventory,
    InterceptGameRun,
    InterceptGameStatus,
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
        self.duel_repository = SpyDuelRepository(settings)
        self._startup_expired = ()

    async def initialize(self, *, now: datetime | None = None) -> None:
        await self.database.initialize()
        current = now or utc_now()
        self._startup_expired = await self.database.transaction(
            lambda connection: self.repository.reconcile(connection, current),
            immediate=True,
        )
        await self.database.transaction(
            lambda connection: self.duel_repository.reconcile(connection, current),
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
        current = now or utc_now()
        await self.database.transaction(
            lambda connection: self.duel_repository.reconcile(connection, current),
            immediate=True,
        )
        if not self.settings.enabled:
            return TickResult()
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

    async def start_intercept_game(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> InterceptGameRun:
        if not self.chat_is_available(chat_id):
            return InterceptGameRun(InterceptGameStatus.DISABLED)
        token = secrets.token_urlsafe(32)
        token_hash = self._game_token_hash(token)
        run_id = secrets.token_hex(12)
        targets = tuple(
            self.rng.randint(15, 85) for _ in range(self.settings.intercept_game_rounds)
        )
        current = now or utc_now()
        result = await self.database.transaction(
            lambda connection: self.repository.start_intercept_game(
                connection,
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                run_id=run_id,
                token_hash=token_hash,
                targets=targets,
                now=current,
            ),
            immediate=True,
        )
        if result.status is InterceptGameStatus.READY:
            return replace(result, launch_token=token)
        return result

    async def start_dead_drop_game(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> DeadDropGameRun:
        if not self.chat_is_available(chat_id):
            return DeadDropGameRun(DeadDropGameStatus.DISABLED)
        token = secrets.token_urlsafe(32)
        token_hash = self._game_token_hash(token)
        run_id = secrets.token_hex(12)
        code = tuple(
            self.rng.randint(0, 9)
            for _ in range(self.settings.dead_drop_game_code_length)
        )
        current = now or utc_now()
        result = await self.database.transaction(
            lambda connection: self.repository.start_dead_drop_game(
                connection,
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                run_id=run_id,
                token_hash=token_hash,
                code=code,
                now=current,
            ),
            immediate=True,
        )
        if result.status is DeadDropGameStatus.READY:
            return replace(result, launch_token=token)
        return result

    async def get_dead_drop_game(
        self,
        launch_token: str,
        *,
        now: datetime | None = None,
    ) -> DeadDropGameRun:
        if not self.settings.enabled:
            return DeadDropGameRun(DeadDropGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return DeadDropGameRun(DeadDropGameStatus.NOT_FOUND)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.get_dead_drop_game(
                connection,
                self._game_token_hash(launch_token),
                current,
            ),
            immediate=True,
        )

    async def guess_dead_drop_game(
        self,
        launch_token: str,
        guess: tuple[int, ...],
        *,
        now: datetime | None = None,
    ) -> DeadDropGameRun:
        if not self.settings.enabled:
            return DeadDropGameRun(DeadDropGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return DeadDropGameRun(DeadDropGameStatus.NOT_FOUND)
        if len(guess) != self.settings.dead_drop_game_code_length or any(
            type(value) is not int or not 0 <= value <= 9 for value in guess
        ):
            raise ValueError("invalid dead drop code")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.guess_dead_drop_game(
                connection,
                self._game_token_hash(launch_token),
                guess,
                current,
            ),
            immediate=True,
        )

    async def get_intercept_game(
        self,
        launch_token: str,
        *,
        now: datetime | None = None,
    ) -> InterceptGameRun:
        if not self.settings.enabled:
            return InterceptGameRun(InterceptGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.get_intercept_game(
                connection,
                self._game_token_hash(launch_token),
                current,
            ),
            immediate=True,
        )

    async def finish_intercept_game(
        self,
        launch_token: str,
        locks: tuple[int, ...],
        *,
        now: datetime | None = None,
    ) -> InterceptGameRun:
        if not self.settings.enabled:
            return InterceptGameRun(InterceptGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        if len(locks) > self.settings.intercept_game_rounds or any(
            type(value) is not int or not 0 <= value <= 100 for value in locks
        ):
            raise ValueError("invalid intercept locks")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.finish_intercept_game(
                connection,
                self._game_token_hash(launch_token),
                locks,
                current,
            ),
            immediate=True,
        )

    @staticmethod
    def _game_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

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

    async def exchange_with_contact(
        self,
        *,
        operation_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> ContactExchangeResult:
        if not self.chat_is_available(chat_id):
            return ContactExchangeResult(
                NpcStatus.DISABLED,
                operation_id,
                recipe_id,
            )
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", operation_id):
            raise ValueError("contact operation ID is invalid")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.exchange_with_contact(
                connection,
                operation_id,
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
