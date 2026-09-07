"""Spy Clicker missions use cases and transaction boundaries."""
from __future__ import annotations

import re
import secrets
from dataclasses import replace
from ..death_mission_repository import DeathMissionRun
from .base import UseCases, utc_now


class MissionsUseCases(UseCases):
    async def start_death_mission(
        self, *, chat_id, message_id, user_id, username, display_name, now=None
    ) -> DeathMissionRun:
        if not self.chat_is_available(chat_id):
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "disabled"}
            )
        token = secrets.token_urlsafe(32)
        result = await self.database.transaction(
            lambda connection: self.repository.death_mission.start(
                connection,
                chat_id=chat_id,
                message_id=message_id,
                user_id=user_id,
                username=username,
                display_name=display_name,
                token_hash=self._game_token_hash(token),
                now=now or utc_now(),
            ),
            immediate=True,
        )
        if "revision" in result.payload:
            return replace(result, launch_token=token)
        return result

    async def get_death_mission(self, token, *, now=None) -> DeathMissionRun:
        if not isinstance(token, str) or not token or len(token) > 256:
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "not_found"}
            )
        return await self.database.transaction(
            lambda connection: self.repository.death_mission.get(
                connection,
                self._game_token_hash(token),
                now or utc_now(),
            ),
            immediate=True,
        )

    @staticmethod
    def _validate_mission_action(action, revision, operation_id, choice):
        if action not in {"arm", "back", "commit", "action", "extract", "abandon"}:
            raise ValueError("Неизвестное действие")
        if type(revision) is not int or revision < 0:
            raise ValueError("Некорректная revision")
        if not isinstance(operation_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", operation_id
        ):
            raise ValueError("Некорректный ключ операции")
        if (
            not isinstance(choice, dict)
            or len(choice) > 3
            or any(
                k not in {"id", "mode", "tactic", "bonus"}
                or not isinstance(v, str)
                or len(v) > 32
                for k, v in choice.items()
            )
        ):
            raise ValueError("Некорректный выбор")

    async def mutate_death_mission(
        self, token, *, action, revision, operation_id, choice, now=None
    ):
        self._validate_mission_action(action, revision, operation_id, choice)
        if not isinstance(token, str) or not token or len(token) > 256:
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "not_found"}
            )
        return await self.database.transaction(
            lambda connection: self.repository.death_mission.mutate(
                connection,
                token_hash=self._game_token_hash(token),
                action=action,
                revision=revision,
                operation_id=operation_id,
                choice=choice,
                now=now or utc_now(),
            ),
            immediate=True,
        )

    async def mission_callback(
        self,
        *,
        run_id,
        user_id,
        chat_id,
        message_id,
        action,
        revision,
        operation_id,
        choice,
        now=None,
    ):
        self._validate_mission_action(action, revision, operation_id, choice)

        def operation(connection):
            row = self.repository.death_mission.row(connection, run_id=run_id)
            if not row or (row["user_id"], row["chat_id"], row["message_id"]) != (
                user_id,
                chat_id,
                message_id,
            ):
                return DeathMissionRun(
                    {"game_type": "death_operation", "status": "forbidden"}
                )
            return self.repository.death_mission.mutate(
                connection,
                token_hash=row["token_hash"],
                action=action,
                revision=revision,
                operation_id=operation_id,
                choice=choice,
                now=now or utc_now(),
            )

        return await self.database.transaction(operation, immediate=True)

    async def death_mission_run_id(self, token):
        return await self.database.read(
            lambda connection: self.repository.death_mission.row(
                connection, token_hash=self._game_token_hash(token)
            )["id"]
        )

    async def reserved_mission_agents(self, user_id):
        return await self.database.read(
            lambda connection: self.repository.death_mission.bundle(
                dict(
                    connection.execute(
                        "SELECT s.agent_type, SUM(s.amount) FROM death_mission_stakes s "
                        "JOIN death_mission_runs r ON r.id=s.run_id "
                        "WHERE r.user_id=? AND r.status='in_run' GROUP BY s.agent_type",
                        (user_id,),
                    ).fetchall()
                )
            )
        )
