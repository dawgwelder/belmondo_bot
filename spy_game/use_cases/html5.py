"""Spy Clicker html5 use cases and transaction boundaries."""
from __future__ import annotations

import re
import secrets
from dataclasses import replace
from datetime import datetime
from ..death_mission_repository import DeathMissionRun
from ..models import (
    DeadDropGameRun,
    DeadDropGameStatus,
    FindMoleGameRun,
    FindMoleGameStatus,
    InterceptGameRun,
    InterceptGameStatus,
)
from .base import UseCases, utc_now, UseCaseContext
from .missions import MissionsUseCases


class Html5UseCases(UseCases):
    def __init__(self, context: UseCaseContext, *, missions: MissionsUseCases) -> None:
        super().__init__(context)
        self.missions = missions

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
            lambda connection: self.repository.intercept.start_intercept_game(
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

    async def start_html5_game(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> InterceptGameRun | DeadDropGameRun | FindMoleGameRun | DeathMissionRun:
        event_type = await self.database.read(
            lambda connection: self.repository.lifecycle.html5_event_type(
                connection,
                chat_id,
                message_id,
            )
        )
        arguments = {
            "chat_id": chat_id,
            "message_id": message_id,
            "user_id": user_id,
            "username": username,
            "display_name": display_name,
            "now": now,
        }
        if event_type == "intercept":
            return await self.start_intercept_game(**arguments)
        if event_type == "dead_drop":
            return await self.start_dead_drop_game(**arguments)
        if event_type == "find_mole":
            return await self.start_find_mole_game(**arguments)
        if event_type == "death_operation":
            return await self.missions.start_death_mission(**arguments)
        return InterceptGameRun(InterceptGameStatus.NOT_FOUND)

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
            lambda connection: self.repository.dead_drop.start_dead_drop_game(
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
            lambda connection: self.repository.dead_drop.get_dead_drop_game(
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
            lambda connection: self.repository.dead_drop.guess_dead_drop_game(
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
            lambda connection: self.repository.intercept.get_intercept_game(
                connection,
                self._game_token_hash(launch_token),
                current,
            ),
            immediate=True,
        )

    async def start_find_mole_game(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime | None = None,
    ) -> FindMoleGameRun:
        if not self.chat_is_available(chat_id) or not self.settings.html5_mole_enabled:
            return FindMoleGameRun(FindMoleGameStatus.DISABLED)
        token = secrets.token_urlsafe(32)
        token_hash = self._game_token_hash(token)
        run_id = secrets.token_hex(12)
        current = now or utc_now()
        result = await self.database.transaction(
            lambda connection: self.repository.find_mole.start_find_mole_game(
                connection,
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                run_id=run_id,
                token_hash=token_hash,
                now=current,
            ),
            immediate=True,
        )
        if result.status is FindMoleGameStatus.READY:
            return replace(result, launch_token=token)
        return result

    async def get_find_mole_game(
        self,
        launch_token: str,
        *,
        now: datetime | None = None,
    ) -> FindMoleGameRun:
        if not self.settings.enabled or not self.settings.html5_mole_enabled:
            return FindMoleGameRun(FindMoleGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND)
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.find_mole.get_find_mole_game(
                connection,
                self._game_token_hash(launch_token),
                current,
            ),
            immediate=True,
        )

    async def get_html5_game(
        self,
        launch_token: str,
        *,
        now: datetime | None = None,
    ) -> tuple[
        str | None,
        InterceptGameRun | DeadDropGameRun | FindMoleGameRun | DeathMissionRun,
    ]:
        if not launch_token or len(launch_token) > 256:
            return None, InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        token_hash = self._game_token_hash(launch_token)
        game_type = await self.database.read(
            lambda connection: self.repository.lifecycle.html5_game_type(
                connection, token_hash
            )
        )
        if game_type == "intercept":
            return game_type, await self.get_intercept_game(launch_token, now=now)
        if game_type == "dead_drop":
            return game_type, await self.get_dead_drop_game(launch_token, now=now)
        if game_type == "find_mole":
            return game_type, await self.get_find_mole_game(launch_token, now=now)
        if game_type == "death_operation":
            return game_type, await self.missions.get_death_mission(
                launch_token, now=now
            )
        return None, InterceptGameRun(InterceptGameStatus.NOT_FOUND)

    async def accuse_find_mole_game(
        self,
        launch_token: str,
        suspect_id: str,
        revision: int,
        idempotency_key: str,
        *,
        now: datetime | None = None,
    ) -> FindMoleGameRun:
        if not self.settings.enabled or not self.settings.html5_mole_enabled:
            return FindMoleGameRun(FindMoleGameStatus.DISABLED)
        if not launch_token or len(launch_token) > 256:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND)
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", suspect_id):
            raise ValueError("invalid mole suspect")
        if type(revision) is not int or revision < 0:
            raise ValueError("invalid mole revision")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", idempotency_key):
            raise ValueError("invalid mole idempotency key")
        current = now or utc_now()
        return await self.database.transaction(
            lambda connection: self.repository.find_mole.accuse_find_mole_game(
                connection,
                self._game_token_hash(launch_token),
                suspect_id,
                revision,
                idempotency_key,
                current,
            ),
            immediate=True,
        )

    async def accuse_find_mole_event(
        self,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        suspect_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> FindMoleGameRun:
        if not self.chat_is_available(chat_id):
            return FindMoleGameRun(FindMoleGameStatus.DISABLED, event_id=event_id)
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", suspect_id):
            return FindMoleGameRun(
                FindMoleGameStatus.INVALID_SUSPECT,
                event_id=event_id,
            )
        current = now or utc_now()
        run_id = secrets.token_hex(12)
        token_hash = self._game_token_hash(f"telegram:{event_id}:{user_id}:{run_id}")
        return await self.database.transaction(
            lambda connection: self.repository.find_mole.accuse_find_mole_event(
                connection,
                event_id=event_id,
                chat_id=chat_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                suspect_id=suspect_id,
                run_id=run_id,
                token_hash=token_hash,
                idempotency_key=idempotency_key,
                now=current,
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
            lambda connection: self.repository.intercept.finish_intercept_game(
                connection,
                self._game_token_hash(launch_token),
                locks,
                current,
            ),
            immediate=True,
        )
