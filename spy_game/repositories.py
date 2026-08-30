"""SQLite persistence operations for Spy Clicker."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

from .models import (
    AdminResult,
    AgentHolding,
    ChatStatus,
    ClaimResult,
    ClaimStatus,
    ExpiredEvent,
    Profile,
    SpawnEvent,
    TickResult,
)
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, RandomSource
from .settings import SpySettings


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _new_event_id() -> str:
    return uuid.uuid4().hex[:12]


class SpyRepository:
    def __init__(
        self,
        settings: SpySettings,
        activity_policy: ActivityPolicy,
        rng: RandomSource,
        reward_resolver: RewardResolver,
        event_id_factory: Callable[[], str] = _new_event_id,
    ) -> None:
        self.settings = settings
        self.activity_policy = activity_policy
        self.rng = rng
        self.reward_resolver = reward_resolver
        self.event_id_factory = event_id_factory

    def reconcile(
        self, connection: sqlite3.Connection, now: datetime
    ) -> tuple[ExpiredEvent, ...]:
        now_value = _iso(now)
        expired = connection.execute(
            """
            SELECT id, chat_id, event_type, message_id
            FROM game_events
            WHERE status = 'active' AND expires_at <= ?
            """,
            (now_value,),
        ).fetchall()
        expired_events: list[ExpiredEvent] = []
        for row in expired:
            self._expire_row(connection, row, now_value)
            expired_events.append(
                ExpiredEvent(row["id"], row["chat_id"], row["message_id"])
            )
        orphaned = connection.execute(
            """
            SELECT id, chat_id, event_type
            FROM game_events
            WHERE status = 'active' AND message_id IS NULL AND expires_at > ?
            """,
            (now_value,),
        ).fetchall()
        for row in orphaned:
            connection.execute(
                """
                UPDATE game_events
                SET status = 'cancelled', resolved_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now_value, row["id"]),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO event_history(
                    idempotency_key, event_id, chat_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'startup_reconciliation', ?)
                """,
                (
                    f"reconcile:{row['id']}",
                    row["id"],
                    row["chat_id"],
                    row["event_type"],
                    now_value,
                ),
            )
        return tuple(expired_events)

    def enable_chat(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: datetime,
    ) -> AdminResult:
        now_value = _iso(now)
        connection.execute(
            """
            INSERT INTO chat_state(
                chat_id, enabled, activity_score, activity_updated_at, updated_at
            ) VALUES (?, 1, 0, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (chat_id, now_value, now_value),
        )
        return AdminResult(True, "Spy Clicker включён в этом чате.")

    def disable_chat(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: datetime,
    ) -> AdminResult:
        now_value = _iso(now)
        connection.execute(
            """
            INSERT INTO chat_state(
                chat_id, enabled, activity_score, activity_updated_at, updated_at
            ) VALUES (?, 0, 0, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = 0,
                next_event_at = NULL,
                updated_at = excluded.updated_at
            """,
            (chat_id, now_value, now_value),
        )
        active = connection.execute(
            """
            SELECT id, event_type, message_id
            FROM game_events
            WHERE chat_id = ? AND status = 'active'
            """,
            (chat_id,),
        ).fetchone()
        message_id = None
        if active is not None:
            connection.execute(
                """
                UPDATE game_events
                SET status = 'cancelled', resolved_at = ?
                WHERE id = ? AND status = 'active'
                """,
                (now_value, active["id"]),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO event_history(
                    idempotency_key, event_id, chat_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'cancelled', ?)
                """,
                (
                    f"cancel:{active['id']}",
                    active["id"],
                    chat_id,
                    active["event_type"],
                    now_value,
                ),
            )
            message_id = active["message_id"]
        return AdminResult(
            True,
            "Spy Clicker выключен. Новые события не появятся.",
            message_id_to_close=message_id,
        )

    def run_tick(
        self,
        connection: sqlite3.Connection,
        activity_counts: Mapping[int, int],
        allowed_chat_ids: frozenset[int],
        now: datetime,
        event_type: str,
    ) -> TickResult:
        now_value = _iso(now)
        self._apply_activity(connection, activity_counts, now, now_value)

        expired_rows = connection.execute(
            """
            SELECT id, chat_id, event_type, message_id
            FROM game_events
            WHERE status = 'active' AND expires_at <= ?
            """,
            (now_value,),
        ).fetchall()
        expired: list[ExpiredEvent] = []
        for row in expired_rows:
            self._expire_row(connection, row, now_value)
            expired.append(ExpiredEvent(row["id"], row["chat_id"], row["message_id"]))

        spawned: list[SpawnEvent] = []
        if allowed_chat_ids:
            placeholders = ",".join("?" for _ in allowed_chat_ids)
            due_rows = connection.execute(
                f"""
                SELECT chat_id
                FROM chat_state
                WHERE enabled = 1
                  AND next_event_at IS NOT NULL
                  AND next_event_at <= ?
                  AND chat_id IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM game_events
                      WHERE game_events.chat_id = chat_state.chat_id
                        AND game_events.status = 'active'
                  )
                ORDER BY next_event_at, chat_id
                LIMIT 20
                """,
                (
                    now_value,
                    *sorted(allowed_chat_ids),
                ),
            ).fetchall()
            for row in due_rows:
                event = self._insert_event(
                    connection,
                    row["chat_id"],
                    now,
                    event_type=event_type,
                    manual=False,
                )
                if event is not None:
                    spawned.append(event)

        return TickResult(tuple(spawned), tuple(expired))

    def _apply_activity(
        self,
        connection: sqlite3.Connection,
        counts: Mapping[int, int],
        now: datetime,
        now_value: str,
    ) -> None:
        for chat_id, message_count in counts.items():
            if message_count <= 0:
                continue
            row = connection.execute(
                """
                SELECT activity_score, activity_updated_at, next_event_at
                FROM chat_state
                WHERE chat_id = ? AND enabled = 1
                """,
                (chat_id,),
            ).fetchone()
            if row is None:
                continue
            score = self.activity_policy.update_score(
                row["activity_score"],
                _datetime(row["activity_updated_at"]),
                message_count,
                now,
            )
            next_event_at = _datetime(row["next_event_at"])
            delay = self.activity_policy.event_delay_seconds(score, self.rng)
            if delay is not None:
                proposed = now + timedelta(seconds=delay)
                if next_event_at is None or proposed < next_event_at:
                    next_event_at = proposed
            connection.execute(
                """
                UPDATE chat_state
                SET activity_score = ?, activity_updated_at = ?,
                    next_event_at = ?, updated_at = ?
                WHERE chat_id = ?
                """,
                (
                    score,
                    now_value,
                    _iso(next_event_at) if next_event_at else None,
                    now_value,
                    chat_id,
                ),
            )

    def manual_spawn(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: datetime,
        event_type: str,
    ) -> AdminResult:
        event = self._insert_event(
            connection,
            chat_id,
            now,
            event_type=event_type,
            manual=True,
        )
        if event is None:
            return AdminResult(
                False,
                "Нельзя создать событие: игра выключена или уже есть активное.",
            )
        return AdminResult(True, "Тестовое событие подготовлено.", event=event)

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: datetime,
        *,
        event_type: str,
        manual: bool,
    ) -> SpawnEvent | None:
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return None
        active = connection.execute(
            "SELECT 1 FROM game_events WHERE chat_id = ? AND status = 'active'",
            (chat_id,),
        ).fetchone()
        if active is not None:
            return None
        event_id = self.event_id_factory()
        expires_at = now + timedelta(seconds=self.settings.event_lifetime_seconds)
        payload = json.dumps(
            {
                "action": "claim",
                "reward_pool": "basic_recruitment",
                "manual": manual,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO game_events(
                id, chat_id, event_type, status, payload_json,
                created_at, expires_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?)
            """,
            (event_id, chat_id, event_type, payload, _iso(now), _iso(expires_at)),
        )
        connection.execute(
            """
            UPDATE chat_state
            SET activity_score = activity_score * ?,
                last_event_at = ?, next_event_at = NULL, updated_at = ?
            WHERE chat_id = ?
            """,
            (
                self.settings.activity_after_spawn_ratio,
                _iso(now),
                _iso(now),
                chat_id,
            ),
        )
        return SpawnEvent(event_id, chat_id, event_type, expires_at)

    def attach_message(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        message_id: int,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE game_events SET message_id = ?
            WHERE id = ? AND status = 'active' AND message_id IS NULL
            """,
            (message_id, event_id),
        )
        return cursor.rowcount == 1

    def cancel_publication(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        now: datetime,
    ) -> bool:
        row = connection.execute(
            "SELECT chat_id, event_type FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return False
        cursor = connection.execute(
            """
            UPDATE game_events
            SET status = 'cancelled', resolved_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (_iso(now), event_id),
        )
        if cursor.rowcount:
            connection.execute(
                """
                INSERT OR IGNORE INTO event_history(
                    idempotency_key, event_id, chat_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'publication_failed', ?)
                """,
                (
                    f"publish-failed:{event_id}",
                    event_id,
                    row["chat_id"],
                    row["event_type"],
                    _iso(now),
                ),
            )
        return cursor.rowcount == 1

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
            self._expire_row(connection, event, now_value)
            return ClaimResult(ClaimStatus.EXPIRED, event_id)

        connection.execute(
            """
            INSERT INTO users(
                user_id, username, display_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (user_id, username, display_name, now_value, now_value),
        )
        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            current = connection.execute(
                "SELECT status, winner_user_id FROM game_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            status = (
                ClaimStatus.EXPIRED
                if current["status"] == "expired"
                else ClaimStatus.ALREADY_RESOLVED
            )
            return ClaimResult(
                status, event_id, winner_user_id=current["winner_user_id"]
            )

        reputation = connection.execute(
            "SELECT reputation FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        reward = self.reward_resolver.resolve(event["event_type"], reputation)
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
                f"claim:{event_id}",
                event_id,
                chat_id,
                user_id,
                event["event_type"],
                reward.agent_type,
                reward.amount,
                now_value,
            ),
        )
        return ClaimResult(
            ClaimStatus.WON,
            event_id,
            reward=reward,
            winner_user_id=user_id,
        )

    def ensure_user_and_profile(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> Profile:
        now_value = _iso(now)
        connection.execute(
            """
            INSERT INTO users(
                user_id, username, display_name, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (user_id, username, display_name, now_value, now_value),
        )
        row = connection.execute(
            """
            SELECT u.*, COALESCE(SUM(a.amount), 0) AS total_agents
            FROM users u
            LEFT JOIN user_agents a ON a.user_id = u.user_id
            WHERE u.user_id = ?
            GROUP BY u.user_id
            """,
            (user_id,),
        ).fetchone()
        return Profile(
            row["user_id"],
            row["username"],
            row["display_name"],
            row["reputation"],
            row["agency_level"],
            row["total_agents"],
        )

    def get_agents(
        self,
        connection: sqlite3.Connection,
        user_id: int,
    ) -> tuple[AgentHolding, ...]:
        rows = connection.execute(
            """
            SELECT agent_type, amount FROM user_agents
            WHERE user_id = ? AND amount > 0
            ORDER BY agent_type
            """,
            (user_id,),
        ).fetchall()
        return tuple(AgentHolding(row["agent_type"], row["amount"]) for row in rows)

    def get_chat_status(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
    ) -> ChatStatus:
        chat = connection.execute(
            "SELECT * FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        active = connection.execute(
            """
            SELECT id, expires_at FROM game_events
            WHERE chat_id = ? AND status = 'active'
            ORDER BY created_at DESC LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        return ChatStatus(
            chat_id=chat_id,
            enabled=bool(chat["enabled"]) if chat else False,
            activity_score=float(chat["activity_score"]) if chat else 0.0,
            next_event_at=_datetime(chat["next_event_at"]) if chat else None,
            active_event_id=active["id"] if active else None,
            active_event_expires_at=_datetime(active["expires_at"]) if active else None,
        )

    @staticmethod
    def _expire_row(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now_value: str,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE game_events
            SET status = 'expired', resolved_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (now_value, row["id"]),
        )
        if cursor.rowcount:
            connection.execute(
                """
                INSERT OR IGNORE INTO event_history(
                    idempotency_key, event_id, chat_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'expired', ?)
                """,
                (
                    f"expire:{row['id']}",
                    row["id"],
                    row["chat_id"],
                    row["event_type"],
                    now_value,
                ),
            )
