"""Chase persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timedelta

from ..chase_rules import CHASE_SECONDS, chase_bonus
from ..models import ChaseResult, ChaseStatus, DropReward
from .economy import EconomyRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class ChaseRepository(RepositoryComponent):
    def __init__(self, context: RepositoryContext, *, economy: EconomyRepository):
        super().__init__(context)
        self.economy = economy

    def get_result(self, connection, event_id):
        row = connection.execute(
            """SELECT c.*, e.chat_id, e.message_id, e.status, u.username, u.display_name
               FROM chase_rounds c JOIN game_events e ON e.id = c.event_id
               JOIN users u ON u.user_id = c.leader_user_id WHERE c.event_id = ?""",
            (event_id,),
        ).fetchone()
        if row is None:
            return ChaseResult(ChaseStatus.NOT_FOUND, event_id)
        return ChaseResult(
            ChaseStatus.COMPLETED
            if row["status"] == "resolved"
            else ChaseStatus.STARTED
            if row["status"] == "active"
            else ChaseStatus.EXPIRED,
            event_id,
            hold_seconds=row["hold_seconds"],
            leader_user_id=row["leader_user_id"],
            leader_name=self.economy.user_label(row["username"], row["display_name"]),
            turn=row["turn"],
            deadline=_datetime(row["deadline"]),
            rewards=tuple(
                DropReward(**reward) for reward in json.loads(row["rewards_json"])
            ),
            chat_id=row["chat_id"],
            message_id=row["message_id"],
        )

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
            "SELECT * FROM game_events WHERE id = ?", (event_id,)
        ).fetchone()
        if event is None or event["event_type"] != "chase":
            return ChaseResult(ChaseStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return ChaseResult(ChaseStatus.WRONG_CHAT, event_id)
        enabled = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if enabled is None or not enabled["enabled"]:
            return ChaseResult(ChaseStatus.DISABLED, event_id)
        if event["status"] != "active":
            return ChaseResult(ChaseStatus.ALREADY_RESOLVED, event_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            if self.finish_event(connection, event_id, now_value):
                return self.get_result(connection, event_id)
            return ChaseResult(ChaseStatus.EXPIRED, event_id)
        current = self.get_result(connection, event_id)
        if current.leader_user_id == user_id:
            return replace(current, status=ChaseStatus.ALREADY_LEADING)
        if current.turn >= len(CHASE_SECONDS):
            return replace(current, status=ChaseStatus.LIMIT_REACHED)
        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        turn = current.turn + 1
        rewards = (*current.rewards, chase_bonus(turn, self.rng))
        hold_seconds = CHASE_SECONDS[turn - 1]
        if current.turn:
            hold_seconds = min(hold_seconds, max(1, current.hold_seconds - 1))
        accelerated = (hold_seconds * 4 + 4) // 5
        if accelerated < hold_seconds and self.economy.equipment.consume(
            connection, user_id, "fake_passport", f"chase:{event_id}:{turn}", now_value
        ):
            hold_seconds = accelerated
        deadline = _iso(now + timedelta(seconds=hold_seconds))
        connection.execute(
            """INSERT INTO chase_rounds(event_id, leader_user_id, turn, deadline, rewards_json, hold_seconds)
               VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(event_id) DO UPDATE SET
               leader_user_id=excluded.leader_user_id, turn=excluded.turn,
               deadline=excluded.deadline, rewards_json=excluded.rewards_json, hold_seconds=excluded.hold_seconds""",
            (
                event_id,
                user_id,
                turn,
                deadline,
                json.dumps([asdict(r) for r in rewards]),
                hold_seconds,
            ),
        )
        connection.execute(
            "UPDATE game_events SET expires_at = ? WHERE id = ?", (deadline, event_id)
        )
        connection.execute(
            """INSERT INTO event_history(idempotency_key, event_id, chat_id, user_id,
               event_type, outcome, metadata_json, created_at)
               VALUES (?, ?, ?, ?, 'chase', 'started', ?, ?)""",
            (
                f"chase-turn:{event_id}:{turn}",
                event_id,
                chat_id,
                user_id,
                json.dumps({"turn": turn}),
                now_value,
            ),
        )
        return self.get_result(connection, event_id)

    def finish_event(self, connection, event_id, now_value):
        result = self.get_result(connection, event_id)
        if (
            result.status is not ChaseStatus.STARTED
            or _iso(result.deadline) > now_value
        ):
            return False
        changed = connection.execute(
            """UPDATE game_events SET status='resolved', winner_user_id=?, resolved_at=?
               WHERE id=? AND status='active'""",
            (result.leader_user_id, now_value, event_id),
        )
        if not changed.rowcount:
            return False
        connection.execute(
            "UPDATE event_participants SET status='resolved', updated_at=? WHERE event_id=? AND status='pending'",
            (now_value, event_id),
        )
        for index, reward in enumerate(result.rewards):
            self.economy.add_drop_reward(connection, result.leader_user_id, reward)
            connection.execute(
                """INSERT INTO event_history(idempotency_key, event_id, chat_id, user_id,
                   event_type, outcome, reward_type, reward_id, reward_amount, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, 'chase', 'rewarded', ?, ?, ?, ?, ?)""",
                (
                    f"chase-prize:{event_id}:{index}",
                    event_id,
                    result.chat_id,
                    result.leader_user_id,
                    reward.reward_type,
                    reward.reward_id,
                    reward.amount,
                    json.dumps(
                        {
                            "role": "starter" if result.turn == 1 else "interceptor",
                            "turn": result.turn,
                        }
                    ),
                    now_value,
                ),
            )
        return True

    def settle_due(self, connection, now):
        now_value = _iso(now)
        for row in connection.execute(
            """SELECT c.event_id FROM chase_rounds c JOIN game_events e ON e.id=c.event_id
               WHERE e.status='active' AND c.deadline <= ?""",
            (now_value,),
        ).fetchall():
            self.finish_event(connection, row["event_id"], now_value)
        return tuple(
            self.get_result(connection, row["event_id"])
            for row in connection.execute(
                """SELECT c.event_id FROM chase_rounds c JOIN game_events e ON e.id=c.event_id
               WHERE e.status='resolved' AND c.notified_at IS NULL AND e.message_id IS NOT NULL"""
            ).fetchall()
        )

    def mark_notified(self, connection, event_id, now):
        connection.execute(
            "UPDATE chase_rounds SET notified_at=? WHERE event_id=?",
            (_iso(now), event_id),
        )
