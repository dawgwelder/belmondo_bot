"""Cooperative persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from ..models import CooperativeResult, CooperativeStatus, Reward
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso


class CooperativeRepository(RepositoryComponent):
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

    def contribute_cooperative(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> CooperativeResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        required = self.settings.cooperative_required_contributions
        if event is None:
            return CooperativeResult(
                CooperativeStatus.NOT_FOUND,
                event_id,
                required_contributions=required,
            )
        if event["chat_id"] != chat_id:
            return CooperativeResult(
                CooperativeStatus.WRONG_CHAT,
                event_id,
                required_contributions=required,
            )
        if event["event_type"] != "cooperative_operation":
            return CooperativeResult(
                CooperativeStatus.NOT_FOUND,
                event_id,
                required_contributions=required,
            )
        if event["status"] == "expired":
            return CooperativeResult(
                CooperativeStatus.EXPIRED,
                event_id,
                required_contributions=required,
            )
        if event["status"] != "active":
            return CooperativeResult(
                CooperativeStatus.ALREADY_RESOLVED,
                event_id,
                required_contributions=required,
            )
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return CooperativeResult(
                CooperativeStatus.EXPIRED,
                event_id,
                required_contributions=required,
            )

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        inserted = connection.execute(
            """
            INSERT OR IGNORE INTO event_participants(
                event_id, user_id, status, payload_json, created_at, updated_at
            ) VALUES (?, ?, 'resolved', '{"contribution":1}', ?, ?)
            """,
            (event_id, user_id, now_value, now_value),
        )
        participant_rows = connection.execute(
            """
            SELECT user_id FROM event_participants
            WHERE event_id = ? AND status = 'resolved'
            ORDER BY created_at, user_id
            """,
            (event_id,),
        ).fetchall()
        participant_ids = tuple(row["user_id"] for row in participant_rows)
        contributions = len(participant_ids)
        if inserted.rowcount != 1:
            return CooperativeResult(
                CooperativeStatus.ALREADY_CONTRIBUTED,
                event_id,
                contributions,
                required,
                participant_ids,
            )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'cooperative_operation',
                      'contributed', ?, ?)
            """,
            (
                f"coop-contribution:{event_id}:{user_id}",
                event_id,
                chat_id,
                user_id,
                json.dumps(
                    {"contributions": contributions, "required": required},
                    separators=(",", ":"),
                ),
                now_value,
            ),
        )
        if contributions < required:
            return CooperativeResult(
                CooperativeStatus.CONTRIBUTED,
                event_id,
                contributions,
                required,
                participant_ids,
            )

        resolved = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ?
              AND event_type = 'cooperative_operation'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if resolved.rowcount != 1:
            return CooperativeResult(
                CooperativeStatus.ALREADY_RESOLVED,
                event_id,
                contributions,
                required,
                participant_ids,
            )
        reward = Reward(
            self.settings.cooperative_reward_agent,
            self.settings.cooperative_reward_amount,
        )
        for participant_id in participant_ids:
            self.economy.add_reward(connection, participant_id, reward)
            connection.execute(
                """
                INSERT INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, reward_type, reward_id, reward_amount,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'cooperative_operation', 'rewarded',
                          'agent', ?, ?, ?, ?)
                """,
                (
                    f"coop-reward:{event_id}:{participant_id}",
                    event_id,
                    chat_id,
                    participant_id,
                    reward.agent_type,
                    reward.amount,
                    json.dumps(
                        {"participants": participant_ids, "required": required},
                        separators=(",", ":"),
                    ),
                    now_value,
                ),
            )
        self.lifecycle.advance_story(
            connection,
            chat_id,
            "cooperative_operation",
            now_value,
        )
        return CooperativeResult(
            CooperativeStatus.COMPLETED,
            event_id,
            contributions,
            required,
            participant_ids,
            reward,
        )
