"""Progression persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from ..models import AgencyResult, AgencyStatus, EconomyStatus, PrestigeResult
from .economy import EconomyRepository
from .base import RepositoryComponent, RepositoryContext, _iso


class ProgressionRepository(RepositoryComponent):
    def __init__(
        self, context: RepositoryContext, *, economy: EconomyRepository
    ) -> None:
        super().__init__(context)
        self.economy = economy

    def increase_reputation(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_reputation: int,
        now: datetime,
    ) -> PrestigeResult:
        now_value = _iso(now)
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return PrestigeResult(EconomyStatus.DISABLED, expected_reputation)
        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        current = connection.execute(
            "SELECT reputation FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        if current != expected_reputation:
            return PrestigeResult(EconomyStatus.STALE, current)
        required = self.settings.prestige_costs(current)
        if not self.economy.has_costs(connection, user_id, required):
            return PrestigeResult(
                EconomyStatus.INSUFFICIENT_RESOURCES,
                current,
                required=required,
            )
        self.economy.spend_costs(connection, user_id, required)
        updated = connection.execute(
            """
            UPDATE users SET reputation = reputation + 1, updated_at = ?
            WHERE user_id = ? AND reputation = ?
            """,
            (now_value, user_id, current),
        )
        if updated.rowcount != 1:
            raise RuntimeError("reputation changed inside serialized transaction")
        metadata = json.dumps(
            {
                "from": current,
                "to": current + 1,
                "costs": {cost.agent_type: cost.amount for cost in required},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO economy_history(
                idempotency_key, user_id, action, recipe_id,
                metadata_json, created_at
            ) VALUES (?, ?, 'prestige', 'reputation', ?, ?)
            """,
            (
                f"prestige:{user_id}:{current}",
                user_id,
                metadata,
                now_value,
            ),
        )
        return PrestigeResult(EconomyStatus.SUCCESS, current + 1, required)

    def found_agency(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_agency_level: int,
        now: datetime,
    ) -> AgencyResult:
        now_value = _iso(now)
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        required_reputation = self.settings.agency_reputation_requirement(
            expected_agency_level
        )
        required_agents = self.settings.agency_requirements(expected_agency_level)
        if chat is None or not chat["enabled"]:
            return AgencyResult(
                AgencyStatus.DISABLED,
                expected_agency_level,
                required_reputation,
                required_agents,
            )
        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        user = connection.execute(
            "SELECT reputation, agency_level FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        current_level = user["agency_level"]
        if current_level != expected_agency_level:
            return AgencyResult(
                AgencyStatus.STALE,
                current_level,
                self.settings.agency_reputation_requirement(current_level),
                self.settings.agency_requirements(current_level),
            )
        if current_level >= self.settings.agency_max_level:
            return AgencyResult(
                AgencyStatus.MAX_LEVEL,
                current_level,
                required_reputation,
                required_agents,
            )
        if user["reputation"] < required_reputation or not self.economy.has_costs(
            connection,
            user_id,
            required_agents,
        ):
            return AgencyResult(
                AgencyStatus.INSUFFICIENT_RESOURCES,
                current_level,
                required_reputation,
                required_agents,
            )

        self.economy.spend_costs(connection, user_id, required_agents)
        updated = connection.execute(
            """
            UPDATE users
            SET agency_level = agency_level + 1, reputation = 0, updated_at = ?
            WHERE user_id = ? AND agency_level = ? AND reputation >= ?
            """,
            (now_value, user_id, current_level, required_reputation),
        )
        if updated.rowcount != 1:
            raise RuntimeError("agency level changed inside serialized transaction")
        metadata = json.dumps(
            {
                "reputation_spent": user["reputation"],
                "agent_costs": {
                    cost.agent_type: cost.amount for cost in required_agents
                },
                "rare_bonus_percent": min(
                    (current_level + 1) * self.settings.agency_rare_bonus_percent,
                    self.settings.agency_max_level
                    * self.settings.agency_rare_bonus_percent,
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO agency_history(
                idempotency_key, user_id, from_level, to_level,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"agency:{user_id}:{current_level}",
                user_id,
                current_level,
                current_level + 1,
                metadata,
                now_value,
            ),
        )
        return AgencyResult(
            AgencyStatus.SUCCESS,
            current_level + 1,
            self.settings.agency_reputation_requirement(current_level + 1),
            self.settings.agency_requirements(current_level + 1),
        )
