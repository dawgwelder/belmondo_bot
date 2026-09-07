"""Death operation persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from ..models import DeathOperationResult, DeathOperationStatus, Reward
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class DeathOperationRepository(RepositoryComponent):
    def __init__(
        self,
        context: RepositoryContext,
        *,
        economy: EconomyRepository,
        lifecycle: LifecycleRepository,
    ) -> None:
        super().__init__(context)
        self.economy = economy
        self.lifecycle = lifecycle

    def run_death_operation(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> DeathOperationResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return DeathOperationResult(DeathOperationStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return DeathOperationResult(DeathOperationStatus.WRONG_CHAT, event_id)
        if (
            json.loads(event["payload_json"]).get("config_id") == "death_choice_v1"
            or connection.execute(
                "SELECT 1 FROM death_mission_runs WHERE user_id=? AND status='in_run'",
                (user_id,),
            ).fetchone()
        ):
            return DeathOperationResult(DeathOperationStatus.INVALID_ACTION, event_id)
        if event["event_type"] != "death_operation":
            return DeathOperationResult(
                DeathOperationStatus.INVALID_ACTION,
                event_id,
            )
        if event["status"] == "expired":
            return DeathOperationResult(DeathOperationStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return DeathOperationResult(
                DeathOperationStatus.ALREADY_RESOLVED,
                event_id,
                winner_user_id=event["winner_user_id"],
            )

        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return DeathOperationResult(DeathOperationStatus.EXPIRED, event_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        staked = self.economy.get_agents(connection, user_id)
        if not staked:
            return DeathOperationResult(
                DeathOperationStatus.INSUFFICIENT_AGENTS,
                event_id,
            )

        stake_payload = [
            {"agent_type": holding.agent_type, "amount": holding.amount}
            for holding in staked
        ]
        participant = connection.execute(
            """
            SELECT status, payload_json FROM event_participants
            WHERE event_id = ? AND user_id = ?
            """,
            (event_id, user_id),
        ).fetchone()
        pending_is_current = False
        if participant is not None and participant["status"] == "pending":
            try:
                pending_payload = json.loads(participant["payload_json"])
                confirmation_expires_at = _datetime(
                    pending_payload.get("confirmation_expires_at")
                )
                pending_is_current = (
                    confirmation_expires_at is not None
                    and confirmation_expires_at > now
                    and pending_payload.get("staked") == stake_payload
                )
            except (AttributeError, TypeError, json.JSONDecodeError, ValueError):
                pending_is_current = False

        if not pending_is_current:
            confirmation_expires_at = min(
                now
                + timedelta(seconds=self.settings.death_operation_confirmation_seconds),
                _datetime(event["expires_at"]),
            )
            payload = json.dumps(
                {
                    "staked": stake_payload,
                    "confirmation_expires_at": _iso(confirmation_expires_at),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO event_participants(
                    event_id, user_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(event_id, user_id) DO UPDATE SET
                    status = 'pending',
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (event_id, user_id, payload, now_value, now_value),
            )
            return DeathOperationResult(
                DeathOperationStatus.CONFIRMATION_REQUIRED,
                event_id,
                staked=staked,
                confirmation_expires_at=confirmation_expires_at,
            )

        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'death_operation'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            current = connection.execute(
                "SELECT status, winner_user_id FROM game_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            status = (
                DeathOperationStatus.EXPIRED
                if current["status"] == "expired"
                else DeathOperationStatus.ALREADY_RESOLVED
            )
            return DeathOperationResult(
                status,
                event_id,
                winner_user_id=current["winner_user_id"],
            )

        connection.execute(
            "UPDATE user_agents SET amount = 0 WHERE user_id = ? AND amount > 0",
            (user_id,),
        )
        roll = self.rng.randint(1, 100)
        won = roll <= self.settings.death_operation_success_percent
        rewards: list[Reward] = []
        if won:
            for holding in staked:
                reward = Reward(
                    holding.agent_type,
                    holding.amount * self.settings.death_operation_reward_multiplier,
                )
                self.economy.add_reward(connection, user_id, reward)
                rewards.append(reward)
            bonus_index = self.rng.randint(
                0,
                len(self.settings.death_operation_bonus_pool) - 1,
            )
            bonus = Reward(self.settings.death_operation_bonus_pool[bonus_index], 1)
            self.economy.add_reward(connection, user_id, bonus)
            rewards.append(bonus)

        outcome = "won" if won else "lost"
        metadata_data = {
            "config_id": "all_in_v1",
            "success_percent": self.settings.death_operation_success_percent,
            "roll": roll,
            "staked": stake_payload,
            "rewards": [
                {"agent_type": reward.agent_type, "amount": reward.amount}
                for reward in rewards
            ],
        }
        metadata = json.dumps(
            metadata_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'death_operation', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"death-operation:{event_id}",
                event_id,
                chat_id,
                user_id,
                outcome,
                "agent_bundle" if won else None,
                (
                    f"all_agents_x{self.settings.death_operation_reward_multiplier}"
                    "+tier3"
                    if won
                    else None
                ),
                sum(reward.amount for reward in rewards) if won else None,
                metadata,
                now_value,
            ),
        )
        connection.execute(
            """
            UPDATE event_participants
            SET status = 'resolved', payload_json = ?, updated_at = ?
            WHERE event_id = ? AND user_id = ?
            """,
            (metadata, now_value, event_id, user_id),
        )
        return DeathOperationResult(
            DeathOperationStatus.WON if won else DeathOperationStatus.LOST,
            event_id,
            staked=staked,
            rewards=tuple(rewards),
            winner_user_id=user_id,
        )
