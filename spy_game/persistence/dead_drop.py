"""Dead drop persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from ..models import (
    ClaimStatus,
    DeadDropGameRun,
    DeadDropGameStatus,
    DeadDropGuess,
    DeadDropResult,
    DropReward,
    Reward,
)
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class DeadDropRepository(RepositoryComponent):
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

    def claim_dead_drop(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> DeadDropResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return DeadDropResult(ClaimStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return DeadDropResult(ClaimStatus.WRONG_CHAT, event_id)
        if event["event_type"] != "dead_drop":
            return DeadDropResult(ClaimStatus.INVALID_ACTION, event_id)
        if event["status"] == "expired":
            return DeadDropResult(ClaimStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return DeadDropResult(
                ClaimStatus.ALREADY_RESOLVED,
                event_id,
                winner_user_id=event["winner_user_id"],
            )
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return DeadDropResult(ClaimStatus.EXPIRED, event_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'dead_drop'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            return DeadDropResult(
                ClaimStatus.ALREADY_RESOLVED,
                event_id,
            )

        reward = self.reward_resolver.resolve_dead_drop(self.rng)
        if reward.reward_type == "item":
            connection.execute(
                """
                INSERT INTO user_items(user_id, item_type, amount)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_type) DO UPDATE SET
                    amount = amount + excluded.amount
                """,
                (user_id, reward.reward_id, reward.amount),
            )
        elif reward.reward_type == "agent":
            self.economy.add_reward(
                connection,
                user_id,
                Reward(reward.reward_id, reward.amount),
            )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount, created_at
            ) VALUES (?, ?, ?, ?, 'dead_drop', 'searched', ?, ?, ?, ?)
            """,
            (
                f"dead-drop:{event_id}",
                event_id,
                chat_id,
                user_id,
                reward.reward_type,
                reward.reward_id,
                reward.amount,
                now_value,
            ),
        )
        return DeadDropResult(
            ClaimStatus.WON,
            event_id,
            reward=reward,
            winner_user_id=user_id,
        )

    def start_dead_drop_game(
        self,
        connection: sqlite3.Connection,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        run_id: str,
        token_hash: str,
        code: tuple[int, ...],
        now: datetime,
    ) -> DeadDropGameRun:
        event = connection.execute(
            """
            SELECT e.*
            FROM game_events e
            JOIN chat_state c ON c.chat_id = e.chat_id
            WHERE e.chat_id = ? AND e.message_id = ?
              AND e.event_type = 'dead_drop' AND c.enabled = 1
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (chat_id, message_id),
        ).fetchone()
        if event is None:
            return DeadDropGameRun(DeadDropGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if event["status"] == "expired" or event["expires_at"] <= now_value:
            if event["status"] == "active":
                self.lifecycle.expire_row(connection, event, now_value)
            return DeadDropGameRun(
                DeadDropGameStatus.EXPIRED,
                event_id=event["id"],
            )
        if event["status"] != "active":
            return DeadDropGameRun(
                DeadDropGameStatus.ALREADY_RESOLVED,
                event_id=event["id"],
            )

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        existing = connection.execute(
            """
            SELECT id, status, expires_at FROM dead_drop_game_runs
            WHERE event_id = ? AND user_id = ?
            """,
            (event["id"], user_id),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "ready":
                return DeadDropGameRun(
                    DeadDropGameStatus.ALREADY_PLAYED,
                    run_id=existing["id"],
                    event_id=event["id"],
                )
            if existing["expires_at"] <= now_value:
                connection.execute(
                    """
                    UPDATE dead_drop_game_runs SET status = 'expired'
                    WHERE id = ? AND status = 'ready'
                    """,
                    (existing["id"],),
                )
                return DeadDropGameRun(
                    DeadDropGameStatus.EXPIRED,
                    run_id=existing["id"],
                    event_id=event["id"],
                )
            connection.execute(
                "UPDATE dead_drop_game_runs SET token_hash = ? WHERE id = ?",
                (token_hash, existing["id"]),
            )
            row = self._dead_drop_game_row(connection, token_hash)
            return self._dead_drop_game_run(row, DeadDropGameStatus.READY)

        run_expires_at = now + timedelta(
            seconds=self.settings.dead_drop_game_run_seconds
        )
        if event["expires_at"] < _iso(run_expires_at):
            connection.execute(
                "UPDATE game_events SET expires_at = ? WHERE id = ?",
                (_iso(run_expires_at), event["id"]),
            )
        connection.execute(
            """
            INSERT INTO dead_drop_game_runs(
                id, event_id, chat_id, message_id, user_id, token_hash,
                code_json, status, started_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
            """,
            (
                run_id,
                event["id"],
                chat_id,
                message_id,
                user_id,
                token_hash,
                json.dumps(code, separators=(",", ":")),
                now_value,
                _iso(run_expires_at),
            ),
        )
        row = self._dead_drop_game_row(connection, token_hash)
        return self._dead_drop_game_run(row, DeadDropGameStatus.READY)

    def get_dead_drop_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        now: datetime,
    ) -> DeadDropGameRun:
        row = self._dead_drop_game_row(connection, token_hash)
        if row is None:
            return DeadDropGameRun(DeadDropGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if row["run_status"] == "ready" and (
            row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value
        ):
            connection.execute(
                """
                UPDATE dead_drop_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._dead_drop_game_run(row, DeadDropGameStatus.EXPIRED)
        return self._dead_drop_game_run(row, self._dead_drop_game_status(row))

    def guess_dead_drop_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        guess: tuple[int, ...],
        now: datetime,
    ) -> DeadDropGameRun:
        row = self._dead_drop_game_row(connection, token_hash)
        if row is None:
            return DeadDropGameRun(DeadDropGameStatus.NOT_FOUND)
        current_status = self._dead_drop_game_status(row)
        if current_status in {DeadDropGameStatus.WON, DeadDropGameStatus.FAILED}:
            return self._dead_drop_game_run(row, current_status)

        now_value = _iso(now)
        if row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value:
            connection.execute(
                """
                UPDATE dead_drop_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._dead_drop_game_run(row, DeadDropGameStatus.EXPIRED)
        if row["event_status"] != "active":
            connection.execute(
                """
                UPDATE dead_drop_game_runs
                SET status = 'lost_race', completed_at = ?
                WHERE id = ? AND status = 'ready'
                """,
                (now_value, row["id"]),
            )
            return self._dead_drop_game_run(
                row,
                DeadDropGameStatus.ALREADY_RESOLVED,
            )

        attempts = self._dead_drop_attempts(row["attempts_json"])
        if any(item.digits == guess for item in attempts):
            return self._dead_drop_game_run(row, DeadDropGameStatus.READY)
        code = tuple(json.loads(row["code_json"]))
        exact = sum(expected == actual for expected, actual in zip(code, guess))
        unmatched_code = Counter(
            expected for expected, actual in zip(code, guess) if expected != actual
        )
        unmatched_guess = Counter(
            actual for expected, actual in zip(code, guess) if expected != actual
        )
        misplaced = sum((unmatched_code & unmatched_guess).values())
        attempts = (*attempts, DeadDropGuess(guess, exact, misplaced))

        reward = None
        run_status = "ready"
        result_status = DeadDropGameStatus.READY
        if exact == self.settings.dead_drop_game_code_length:
            claimed = connection.execute(
                """
                UPDATE game_events
                SET status = 'resolved', winner_user_id = ?, resolved_at = ?
                WHERE id = ? AND status = 'active' AND expires_at > ?
                """,
                (row["user_id"], now_value, row["event_id"], now_value),
            )
            if claimed.rowcount != 1:
                run_status = "lost_race"
                result_status = DeadDropGameStatus.ALREADY_RESOLVED
            else:
                reward = self.reward_resolver.resolve_dead_drop(self.rng)
                self.economy.add_drop_reward(connection, row["user_id"], reward)
                run_status = "won"
                result_status = DeadDropGameStatus.WON
        attempts_json = json.dumps(
            [
                {
                    "digits": item.digits,
                    "exact": item.exact,
                    "misplaced": item.misplaced,
                }
                for item in attempts
            ],
            separators=(",", ":"),
        )
        connection.execute(
            """
            UPDATE dead_drop_game_runs
            SET attempts_json = ?, status = ?, completed_at = ?,
                reward_type = ?, reward_id = ?, reward_amount = ?
            WHERE id = ? AND status = 'ready'
            """,
            (
                attempts_json,
                run_status,
                now_value if run_status != "ready" else None,
                reward.reward_type if reward else None,
                reward.reward_id if reward else None,
                reward.amount if reward else None,
                row["id"],
            ),
        )
        if run_status != "ready":
            metadata = json.dumps(
                {
                    "run_id": row["id"],
                    "attempts": len(attempts),
                    "solved": result_status is DeadDropGameStatus.WON,
                },
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, reward_type, reward_id, reward_amount,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'dead_drop', ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"dead-drop-game:{row['id']}",
                    row["event_id"],
                    row["chat_id"],
                    row["user_id"],
                    result_status.value,
                    reward.reward_type if reward else None,
                    reward.reward_id if reward else None,
                    reward.amount if reward else None,
                    metadata,
                    now_value,
                ),
            )
        return DeadDropGameRun(
            result_status,
            run_id=row["id"],
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            public_name=self.economy.user_label(row["username"], None),
            code_length=self.settings.dead_drop_game_code_length,
            attempts=attempts,
            expires_at=_datetime(row["run_expires_at"]),
            reward=reward,
        )

    def _dead_drop_game_row(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT r.id, r.event_id, r.chat_id, r.message_id, r.user_id,
                   r.code_json, r.attempts_json, r.status AS run_status,
                   r.expires_at AS run_expires_at,
                   r.reward_type, r.reward_id, r.reward_amount,
                   e.status AS event_status,
                   e.expires_at AS event_expires_at,
                   u.username
            FROM dead_drop_game_runs r
            JOIN game_events e ON e.id = r.event_id
            JOIN users u ON u.user_id = r.user_id
            WHERE r.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    @staticmethod
    def _dead_drop_attempts(raw_attempts: str) -> tuple[DeadDropGuess, ...]:
        return tuple(
            DeadDropGuess(
                tuple(item["digits"]),
                item["exact"],
                item["misplaced"],
            )
            for item in json.loads(raw_attempts)
        )

    @staticmethod
    def _dead_drop_game_status(row: sqlite3.Row) -> DeadDropGameStatus:
        if row["run_status"] == "won":
            return DeadDropGameStatus.WON
        if row["run_status"] == "failed":
            return DeadDropGameStatus.FAILED
        if row["run_status"] == "expired":
            return DeadDropGameStatus.EXPIRED
        if row["run_status"] == "lost_race" or row["event_status"] != "active":
            return DeadDropGameStatus.ALREADY_RESOLVED
        return DeadDropGameStatus.READY

    def _dead_drop_game_run(
        self,
        row: sqlite3.Row,
        status: DeadDropGameStatus,
    ) -> DeadDropGameRun:
        reward = (
            DropReward(row["reward_type"], row["reward_id"], row["reward_amount"])
            if row["reward_type"] is not None
            else None
        )
        return DeadDropGameRun(
            status,
            run_id=row["id"],
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            public_name=self.economy.user_label(row["username"], None),
            code_length=self.settings.dead_drop_game_code_length,
            attempts=self._dead_drop_attempts(row["attempts_json"]),
            expires_at=_datetime(row["run_expires_at"]),
            reward=reward,
        )
