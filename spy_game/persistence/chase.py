"""Chase persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from ..models import ChaseResult, ChaseStatus, Reward
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso


class ChaseRepository(RepositoryComponent):
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

    def advance_chase(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> ChaseResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None or event["event_type"] != "chase":
            return ChaseResult(ChaseStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return ChaseResult(ChaseStatus.WRONG_CHAT, event_id)
        if event["status"] == "expired":
            return ChaseResult(ChaseStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return ChaseResult(ChaseStatus.ALREADY_RESOLVED, event_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return ChaseResult(ChaseStatus.EXPIRED, event_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        starter = connection.execute(
            """
            SELECT p.user_id, u.username, u.display_name
            FROM event_participants p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.event_id = ? AND p.status = 'pending'
            ORDER BY p.created_at, p.user_id LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if starter is None:
            connection.execute(
                """
                INSERT INTO event_participants(
                    event_id, user_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', '{"stage":1}', ?, ?)
                """,
                (event_id, user_id, now_value, now_value),
            )
            connection.execute(
                """
                INSERT INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'chase', 'started', ?)
                """,
                (f"chase-start:{event_id}", event_id, chat_id, user_id, now_value),
            )
            return ChaseResult(
                ChaseStatus.STARTED,
                event_id,
                starter_user_id=user_id,
                starter_name=self.economy.user_label(username, display_name),
            )

        starter_user_id = starter["user_id"]
        starter_name = self.economy.user_label(
            starter["username"],
            starter["display_name"],
        )
        interceptor_name = self.economy.user_label(username, display_name)
        resolved = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'chase'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if resolved.rowcount != 1:
            return ChaseResult(ChaseStatus.ALREADY_RESOLVED, event_id)
        connection.execute(
            """
            UPDATE event_participants
            SET status = 'resolved', payload_json = '{"stage":2}', updated_at = ?
            WHERE event_id = ? AND user_id = ? AND status = 'pending'
            """,
            (now_value, event_id, starter_user_id),
        )
        starter_reward = Reward(
            self.settings.chase_starter_reward.agent_type,
            self.settings.chase_starter_reward.amount,
        )
        interceptor_reward = Reward(
            self.settings.chase_interceptor_reward.agent_type,
            self.settings.chase_interceptor_reward.amount,
        )
        for participant_id, role, reward in (
            (starter_user_id, "starter", starter_reward),
            (user_id, "interceptor", interceptor_reward),
        ):
            self.economy.add_reward(connection, participant_id, reward)
            connection.execute(
                """
                INSERT INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, reward_type, reward_id, reward_amount,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'chase', 'rewarded', 'agent', ?, ?, ?, ?)
                """,
                (
                    f"chase-reward:{event_id}:{role}",
                    event_id,
                    chat_id,
                    participant_id,
                    reward.agent_type,
                    reward.amount,
                    json.dumps({"role": role}, separators=(",", ":")),
                    now_value,
                ),
            )
        return ChaseResult(
            status=ChaseStatus.COMPLETED,
            event_id=event_id,
            starter_user_id=starter_user_id,
            interceptor_user_id=user_id,
            starter_reward=starter_reward,
            interceptor_reward=interceptor_reward,
            starter_name=starter_name,
            interceptor_name=interceptor_name,
        )
