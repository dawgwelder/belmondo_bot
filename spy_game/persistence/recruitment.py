"""Recruitment persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from ..models import ClaimResult, ClaimStatus, RecruitmentProgress, Reward
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso


class RecruitmentRepository(RepositoryComponent):
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

    def claim(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> ClaimResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return ClaimResult(ClaimStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return ClaimResult(ClaimStatus.WRONG_CHAT, event_id)
        if event["event_type"] != "recruitment":
            return ClaimResult(ClaimStatus.INVALID_ACTION, event_id)
        if event["status"] == "expired":
            return ClaimResult(ClaimStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return ClaimResult(
                ClaimStatus.ALREADY_RESOLVED,
                event_id,
                winner_user_id=event["winner_user_id"],
            )
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return ClaimResult(ClaimStatus.EXPIRED, event_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        required_claims = self.settings.recruitment_winner_count
        participant = connection.execute(
            """
            INSERT OR IGNORE INTO event_participants(
                event_id, user_id, status, payload_json, created_at, updated_at
            ) VALUES (?, ?, 'resolved', '{"role":"recruit"}', ?, ?)
            """,
            (event_id, user_id, now_value, now_value),
        )
        if participant.rowcount != 1:
            claims = connection.execute(
                "SELECT COUNT(*) FROM event_participants WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
            return ClaimResult(
                ClaimStatus.ALREADY_CLAIMED,
                event_id,
                winner_user_id=user_id,
                claims=claims,
                required_claims=required_claims,
            )

        reputation = connection.execute(
            "SELECT reputation FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        reward = self.reward_resolver.resolve(event["event_type"], reputation)
        if self.economy.item_is_equipped(connection, user_id, "wiretap"):
            roll = self.rng.randint(1, 100)
            if roll <= self.settings.wiretap_bonus_chance_percent:
                reward = Reward(reward.agent_type, reward.amount + 1)
        connection.execute(
            """
            INSERT INTO user_agents(user_id, agent_type, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, agent_type) DO UPDATE SET
                amount = amount + excluded.amount
            """,
            (user_id, reward.agent_type, reward.amount),
        )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount, created_at
            ) VALUES (?, ?, ?, ?, ?, 'won', 'agent', ?, ?, ?)
            """,
            (
                f"claim:{event_id}:{user_id}",
                event_id,
                chat_id,
                user_id,
                event["event_type"],
                reward.agent_type,
                reward.amount,
                now_value,
            ),
        )
        claims = connection.execute(
            "SELECT COUNT(*) FROM event_participants WHERE event_id = ?",
            (event_id,),
        ).fetchone()[0]
        if claims >= required_claims:
            resolved = connection.execute(
                """
                UPDATE game_events
                SET status = 'resolved', winner_user_id = ?, resolved_at = ?
                WHERE id = ? AND chat_id = ? AND event_type = 'recruitment'
                  AND status = 'active' AND expires_at > ?
                """,
                (user_id, now_value, event_id, chat_id, now_value),
            )
            if resolved.rowcount != 1:
                raise RuntimeError("recruitment changed inside serialized transaction")
        return ClaimResult(
            ClaimStatus.WON,
            event_id,
            reward=reward,
            winner_user_id=user_id,
            claims=claims,
            required_claims=required_claims,
        )

    def get_recruitment_progress(
        self,
        connection: sqlite3.Connection,
        event_id: str,
    ) -> RecruitmentProgress | None:
        event = connection.execute(
            """
            SELECT status FROM game_events
            WHERE id = ? AND event_type = 'recruitment'
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            return None
        rows = connection.execute(
            """
            SELECT u.username
            FROM event_participants p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.event_id = ?
            ORDER BY p.created_at, p.user_id
            """,
            (event_id,),
        ).fetchall()
        usernames = tuple(
            f"@{row['username'].lstrip('@')}"
            for row in rows
            if row["username"] and row["username"].strip("@")
        )
        claims = len(rows)
        required = self.settings.recruitment_winner_count
        return RecruitmentProgress(
            event_id=event_id,
            claims=claims,
            required_claims=required,
            usernames=usernames,
            completed=event["status"] != "active" or claims >= required,
        )
