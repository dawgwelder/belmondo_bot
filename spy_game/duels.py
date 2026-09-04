"""Persistent escrow and deterministic settlement for wagered duels."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from .models import DuelWager, DuelWagerStatus
from .settings import SpySettings


DUEL_ACTIONS = ("attack", "defend", "trick", "environment", "risk")

# A complete five-action cycle: every action defeats two and loses to two.
DUEL_BEATS = {
    "attack": frozenset(("environment", "risk")),
    "environment": frozenset(("risk", "trick")),
    "risk": frozenset(("trick", "defend")),
    "trick": frozenset(("defend", "attack")),
    "defend": frozenset(("attack", "environment")),
}


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


class SpyDuelRepository:
    def __init__(self, settings: SpySettings) -> None:
        self.settings = settings

    def create(
        self,
        connection: sqlite3.Connection,
        *,
        duel_id: str,
        chat_id: int,
        challenger_user_id: int,
        challenger_username: str | None,
        challenger_display_name: str | None,
        opponent_user_id: int | None,
        opponent_username: str | None,
        opponent_display_name: str | None,
        stake_amount: int,
        scenario: dict,
        tie_breaker_role: str,
        now: datetime,
    ) -> DuelWager:
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return DuelWager(
                DuelWagerStatus.DISABLED,
                duel_id,
                chat_id=chat_id,
            )
        if opponent_user_id == challenger_user_id or (
            opponent_user_id is None
            and self._normalize_username(opponent_username) is None
        ):
            return DuelWager(
                DuelWagerStatus.INVALID_PARTICIPANT,
                duel_id,
            )
        active = connection.execute(
            """
            SELECT id FROM spy_duels
            WHERE chat_id = ? AND status IN ('pending', 'choosing')
            """,
            (chat_id,),
        ).fetchone()
        if active is not None:
            active_row = self._row(connection, active["id"])
            if active_row["expires_at"] <= _iso(now):
                self._expire_row(connection, active_row, now)
            else:
                return DuelWager(
                    DuelWagerStatus.ACTIVE_DUEL_EXISTS,
                    duel_id,
                    chat_id=chat_id,
                )
        now_value = _iso(now)
        self._ensure_user(
            connection,
            challenger_user_id,
            challenger_username,
            challenger_display_name,
            now_value,
        )
        if opponent_user_id is not None:
            self._ensure_user(
                connection,
                opponent_user_id,
                opponent_username,
                opponent_display_name,
                now_value,
            )
        if not self._take_stake(
            connection,
            challenger_user_id,
            self.settings.duel_stake_agent_type,
            stake_amount,
        ):
            return DuelWager(
                DuelWagerStatus.INSUFFICIENT_AGENTS,
                duel_id,
                chat_id=chat_id,
                challenger_user_id=challenger_user_id,
                stake_amount=stake_amount,
            )
        expires_at = now + timedelta(seconds=self.settings.duel_accept_seconds)
        connection.execute(
            """
            INSERT INTO spy_duels(
                id, chat_id, challenger_user_id, opponent_user_id,
                opponent_username, agent_type, stake_amount, status,
                tie_breaker_role, scenario_json, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                duel_id,
                chat_id,
                challenger_user_id,
                opponent_user_id,
                self._normalize_username(opponent_username),
                self.settings.duel_stake_agent_type,
                stake_amount,
                tie_breaker_role,
                json.dumps(scenario, ensure_ascii=False, separators=(",", ":")),
                now_value,
                _iso(expires_at),
            ),
        )
        return self._model(self._row(connection, duel_id))

    def attach_message(
        self,
        connection: sqlite3.Connection,
        duel_id: str,
        message_id: int,
    ) -> DuelWager:
        connection.execute(
            "UPDATE spy_duels SET message_id = ? WHERE id = ?",
            (message_id, duel_id),
        )
        row = self._row(connection, duel_id)
        return (
            DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
            if row is None
            else self._model(row)
        )

    def get(
        self,
        connection: sqlite3.Connection,
        duel_id: str,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"pending", "choosing"} and row["expires_at"] <= _iso(now):
            return self._expire_row(connection, row, now)
        return self._model(row)

    def get_active_for_chat(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        now: datetime,
    ) -> DuelWager:
        active = connection.execute(
            """
            SELECT id FROM spy_duels
            WHERE chat_id = ? AND status IN ('pending', 'choosing')
            LIMIT 1
            """,
            (chat_id,),
        ).fetchone()
        if active is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, "")
        return self.get(connection, active["id"], now)

    def accept(
        self,
        connection: sqlite3.Connection,
        *,
        duel_id: str,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        if row["expires_at"] <= _iso(now):
            return self._expire_row(connection, row, now)
        if row["status"] != "pending" or not self._is_opponent(
            row,
            user_id,
            username,
        ):
            return self._model(row, DuelWagerStatus.INVALID_PARTICIPANT)

        now_value = _iso(now)
        self._ensure_user(connection, user_id, username, display_name, now_value)
        if not self._take_stake(
            connection,
            user_id,
            row["agent_type"],
            row["stake_amount"],
        ):
            return self._model(row, DuelWagerStatus.INSUFFICIENT_AGENTS)
        expires_at = now + timedelta(seconds=self.settings.duel_move_seconds)
        connection.execute(
            """
            UPDATE spy_duels
            SET opponent_user_id = ?, opponent_username = ?, status = 'choosing',
                accepted_at = ?, expires_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                user_id,
                self._normalize_username(username),
                now_value,
                _iso(expires_at),
                duel_id,
            ),
        )
        return self._model(self._row(connection, duel_id))

    def choose(
        self,
        connection: sqlite3.Connection,
        *,
        duel_id: str,
        user_id: int,
        action: str,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        if row["expires_at"] <= _iso(now):
            return self._expire_row(connection, row, now)
        if row["status"] != "choosing" or action not in DUEL_ACTIONS:
            return self._model(row, DuelWagerStatus.INVALID_PARTICIPANT)
        if user_id == row["challenger_user_id"]:
            column = "challenger_action"
        elif user_id == row["opponent_user_id"]:
            column = "opponent_action"
        else:
            return self._model(row, DuelWagerStatus.INVALID_PARTICIPANT)
        if row[column] is not None:
            return self._model(row, DuelWagerStatus.ALREADY_MOVED)

        connection.execute(
            f"UPDATE spy_duels SET {column} = ? WHERE id = ? AND {column} IS NULL",
            (action, duel_id),
        )
        row = self._row(connection, duel_id)
        if row["challenger_action"] is None or row["opponent_action"] is None:
            return self._model(row)
        winner_role = self._winner_role(
            row["challenger_action"],
            row["opponent_action"],
            row["tie_breaker_role"],
        )
        winner_id = row[f"{winner_role}_user_id"]
        return self._settle(connection, row, winner_id, "moves", now)

    def forfeit(
        self,
        connection: sqlite3.Connection,
        duel_id: str,
        user_id: int,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        if row["expires_at"] <= _iso(now):
            return self._expire_row(connection, row, now)
        if row["status"] != "choosing" or user_id not in {
            row["challenger_user_id"],
            row["opponent_user_id"],
        }:
            return self._model(row, DuelWagerStatus.INVALID_PARTICIPANT)
        winner_id = (
            row["opponent_user_id"]
            if user_id == row["challenger_user_id"]
            else row["challenger_user_id"]
        )
        return self._settle(connection, row, winner_id, "forfeit", now)

    def close_pending(
        self,
        connection: sqlite3.Connection,
        *,
        duel_id: str,
        user_id: int,
        username: str | None,
        action: str,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        if row["expires_at"] <= _iso(now):
            return self._expire_row(connection, row, now)
        allowed = (action == "cancel" and user_id == row["challenger_user_id"]) or (
            action == "decline" and self._is_opponent(row, user_id, username)
        )
        if row["status"] != "pending" or not allowed:
            return self._model(row, DuelWagerStatus.INVALID_PARTICIPANT)
        return self._refund(connection, row, action, now)

    def master_cancel(
        self,
        connection: sqlite3.Connection,
        duel_id: str,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        return self._refund(connection, row, "master_cancel", now)

    def expire(
        self,
        connection: sqlite3.Connection,
        duel_id: str,
        now: datetime,
    ) -> DuelWager:
        row = self._row(connection, duel_id)
        if row is None:
            return DuelWager(DuelWagerStatus.NOT_FOUND, duel_id)
        if row["status"] in {"resolved", "refunded"}:
            return self._model(row)
        if row["expires_at"] > _iso(now):
            return self._model(row)
        return self._expire_row(connection, row, now)

    def reconcile(self, connection: sqlite3.Connection, now: datetime) -> None:
        rows = connection.execute(
            """
            SELECT id FROM spy_duels
            WHERE status IN ('pending', 'choosing') AND expires_at <= ?
            """,
            (_iso(now),),
        ).fetchall()
        for row in rows:
            current = self._row(connection, row["id"])
            if current is not None:
                self._expire_row(connection, current, now)

    def _expire_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: datetime,
    ) -> DuelWager:
        if row["status"] == "pending":
            return self._refund(connection, row, "accept_timeout", now)
        if row["challenger_action"] is not None:
            return self._settle(
                connection,
                row,
                row["challenger_user_id"],
                "move_timeout",
                now,
            )
        if row["opponent_action"] is not None:
            return self._settle(
                connection,
                row,
                row["opponent_user_id"],
                "move_timeout",
                now,
            )
        return self._refund(connection, row, "move_timeout_no_moves", now)

    def _settle(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        winner_user_id: int,
        resolution: str,
        now: datetime,
    ) -> DuelWager:
        now_value = _iso(now)
        updated = connection.execute(
            """
            UPDATE spy_duels
            SET status = 'resolved', winner_user_id = ?, resolution = ?,
                resolved_at = ?
            WHERE id = ? AND status = 'choosing'
            """,
            (winner_user_id, resolution, now_value, row["id"]),
        )
        if updated.rowcount == 1:
            pot = row["stake_amount"] * 2
            self._add_agents(
                connection,
                winner_user_id,
                row["agent_type"],
                pot,
            )
            self._history(connection, row, winner_user_id, resolution, pot, now_value)
        return self._model(self._row(connection, row["id"]))

    def _refund(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        resolution: str,
        now: datetime,
    ) -> DuelWager:
        now_value = _iso(now)
        previous_status = row["status"]
        updated = connection.execute(
            """
            UPDATE spy_duels
            SET status = 'refunded', resolution = ?, resolved_at = ?
            WHERE id = ? AND status IN ('pending', 'choosing')
            """,
            (resolution, now_value, row["id"]),
        )
        if updated.rowcount == 1:
            self._add_agents(
                connection,
                row["challenger_user_id"],
                row["agent_type"],
                row["stake_amount"],
            )
            if previous_status == "choosing" and row["opponent_user_id"] is not None:
                self._add_agents(
                    connection,
                    row["opponent_user_id"],
                    row["agent_type"],
                    row["stake_amount"],
                )
            self._history(connection, row, None, resolution, 0, now_value)
        return self._model(self._row(connection, row["id"]))

    @staticmethod
    def _winner_role(challenger: str, opponent: str, tie_breaker: str) -> str:
        if challenger == opponent:
            return tie_breaker
        return "challenger" if opponent in DUEL_BEATS[challenger] else "opponent"

    @staticmethod
    def _take_stake(
        connection: sqlite3.Connection,
        user_id: int,
        agent_type: str,
        amount: int,
    ) -> bool:
        cursor = connection.execute(
            """
            UPDATE user_agents SET amount = amount - ?
            WHERE user_id = ? AND agent_type = ? AND amount >= ?
            """,
            (amount, user_id, agent_type, amount),
        )
        return cursor.rowcount == 1

    @staticmethod
    def _add_agents(
        connection: sqlite3.Connection,
        user_id: int,
        agent_type: str,
        amount: int,
    ) -> None:
        connection.execute(
            """
            INSERT INTO user_agents(user_id, agent_type, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, agent_type) DO UPDATE SET
                amount = amount + excluded.amount
            """,
            (user_id, agent_type, amount),
        )

    @staticmethod
    def _ensure_user(
        connection: sqlite3.Connection,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now_value: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO users(user_id, username, display_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (user_id, username, display_name, now_value, now_value),
        )

    @staticmethod
    def _normalize_username(username: str | None) -> str | None:
        if not username or not username.strip("@"):
            return None
        return username.lstrip("@").lower()

    def _is_opponent(
        self,
        row: sqlite3.Row,
        user_id: int,
        username: str | None,
    ) -> bool:
        if row["opponent_user_id"] is not None:
            return user_id == row["opponent_user_id"]
        return self._normalize_username(username) == row["opponent_username"]

    @staticmethod
    def _history(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        winner_user_id: int | None,
        outcome: str,
        pot_amount: int,
        now_value: str,
    ) -> None:
        metadata = json.dumps(
            {
                "challenger_action": row["challenger_action"],
                "opponent_action": row["opponent_action"],
            },
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO spy_duel_history(
                idempotency_key, duel_id, chat_id, challenger_user_id,
                opponent_user_id, winner_user_id, agent_type, stake_amount,
                pot_amount, outcome, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"duel:{row['id']}",
                row["id"],
                row["chat_id"],
                row["challenger_user_id"],
                row["opponent_user_id"],
                winner_user_id,
                row["agent_type"],
                row["stake_amount"],
                pot_amount,
                outcome,
                metadata,
                now_value,
            ),
        )

    @staticmethod
    def _row(connection: sqlite3.Connection, duel_id: str) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT d.*,
                   challenger.username AS challenger_username,
                   opponent.username AS bound_opponent_username,
                   winner.username AS winner_username
            FROM spy_duels d
            JOIN users challenger ON challenger.user_id = d.challenger_user_id
            LEFT JOIN users opponent ON opponent.user_id = d.opponent_user_id
            LEFT JOIN users winner ON winner.user_id = d.winner_user_id
            WHERE d.id = ?
            """,
            (duel_id,),
        ).fetchone()

    def _model(
        self,
        row: sqlite3.Row,
        status: DuelWagerStatus | None = None,
    ) -> DuelWager:
        if status is None:
            status = {
                "pending": DuelWagerStatus.PENDING,
                "choosing": DuelWagerStatus.CHOOSING,
                "resolved": DuelWagerStatus.WON,
                "refunded": DuelWagerStatus.REFUNDED,
            }[row["status"]]
        opponent_username = row["bound_opponent_username"] or row["opponent_username"]
        return DuelWager(
            status,
            row["id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            challenger_user_id=row["challenger_user_id"],
            opponent_user_id=row["opponent_user_id"],
            challenger_name=self._public_name(row["challenger_username"]),
            opponent_name=self._public_name(opponent_username),
            opponent_username=row["opponent_username"],
            agent_type=row["agent_type"],
            stake_amount=row["stake_amount"],
            challenger_action=row["challenger_action"],
            opponent_action=row["opponent_action"],
            winner_user_id=row["winner_user_id"],
            winner_name=(
                self._public_name(row["winner_username"])
                if row["winner_user_id"] is not None
                else None
            ),
            resolution=row["resolution"],
            scenario=json.loads(row["scenario_json"]),
            expires_at=_datetime(row["expires_at"]),
        )

    @staticmethod
    def _public_name(username: str | None) -> str:
        return (
            f"@{username.lstrip('@')}"
            if username and username.strip("@")
            else "Скрытый агент"
        )
