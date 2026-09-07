"""Find mole persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from ..models import (
    DropReward,
    FindMoleGameRun,
    FindMoleGameStatus,
    MoleSuspectCard,
    Reward,
)
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class FindMoleRepository(RepositoryComponent):
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

    def start_find_mole_game(
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
        now: datetime,
    ) -> FindMoleGameRun:
        event = connection.execute(
            """
            SELECT e.*, cse.public_case_json, cse.solution_suspect_id
            FROM game_events e
            JOIN chat_state c ON c.chat_id = e.chat_id
            JOIN find_mole_cases cse ON cse.event_id = e.id
            WHERE e.chat_id = ? AND e.message_id = ?
              AND e.event_type = 'find_mole' AND c.enabled = 1
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (chat_id, message_id),
        ).fetchone()
        if event is None:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if event["status"] == "expired" or event["expires_at"] <= now_value:
            if event["status"] == "active":
                self.lifecycle.expire_row(connection, event, now_value)
            return FindMoleGameRun(
                FindMoleGameStatus.EXPIRED,
                event_id=event["id"],
            )
        if event["status"] != "active":
            return FindMoleGameRun(
                FindMoleGameStatus.ALREADY_RESOLVED,
                event_id=event["id"],
            )

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        existing = connection.execute(
            """
            SELECT id, status, expires_at FROM find_mole_game_runs
            WHERE event_id = ? AND user_id = ?
            """,
            (event["id"], user_id),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "ready":
                row = self._find_mole_game_row_by_id(connection, existing["id"])
                return self._find_mole_game_run(
                    row,
                    FindMoleGameStatus.ALREADY_PLAYED,
                )
            if existing["expires_at"] <= now_value:
                connection.execute(
                    """
                    UPDATE find_mole_game_runs SET status = 'expired'
                    WHERE id = ? AND status = 'ready'
                    """,
                    (existing["id"],),
                )
                row = self._find_mole_game_row_by_id(connection, existing["id"])
                return self._find_mole_game_run(row, FindMoleGameStatus.EXPIRED)
            connection.execute(
                "UPDATE find_mole_game_runs SET token_hash = ? WHERE id = ?",
                (token_hash, existing["id"]),
            )
            row = self._find_mole_game_row_by_id(connection, existing["id"])
            return self._find_mole_game_run(row, FindMoleGameStatus.READY)

        run_expires_at = min(
            now + timedelta(seconds=self.settings.mole_game_run_seconds),
            _datetime(event["expires_at"]),
        )
        connection.execute(
            """
            INSERT INTO find_mole_game_runs(
                id, event_id, chat_id, message_id, user_id, token_hash,
                status, started_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)
            """,
            (
                run_id,
                event["id"],
                chat_id,
                message_id,
                user_id,
                token_hash,
                now_value,
                _iso(run_expires_at),
            ),
        )
        row = self._find_mole_game_row_by_id(connection, run_id)
        return self._find_mole_game_run(row, FindMoleGameStatus.READY)

    def get_find_mole_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        now: datetime,
    ) -> FindMoleGameRun:
        row = self._find_mole_game_row(connection, token_hash)
        if row is None:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if row["run_status"] == "ready" and (
            row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value
        ):
            connection.execute(
                """
                UPDATE find_mole_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._find_mole_game_run(row, FindMoleGameStatus.EXPIRED)
        return self._find_mole_game_run(row, self._find_mole_game_status(row))

    def accuse_find_mole_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        suspect_id: str,
        revision: int,
        idempotency_key: str,
        now: datetime,
    ) -> FindMoleGameRun:
        row = self._find_mole_game_row(connection, token_hash)
        if row is None:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND)
        return self._resolve_find_mole_accusation(
            connection,
            row,
            suspect_id,
            revision,
            idempotency_key,
            now,
        )

    def accuse_find_mole_event(
        self,
        connection: sqlite3.Connection,
        *,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        suspect_id: str,
        run_id: str,
        token_hash: str,
        idempotency_key: str,
        now: datetime,
    ) -> FindMoleGameRun:
        event = connection.execute(
            """
            SELECT e.*, cse.public_case_json
            FROM game_events e
            JOIN find_mole_cases cse ON cse.event_id = e.id
            WHERE e.id = ?
            """,
            (event_id,),
        ).fetchone()
        if event is None:
            return FindMoleGameRun(FindMoleGameStatus.NOT_FOUND, event_id=event_id)
        if event["chat_id"] != chat_id:
            return FindMoleGameRun(FindMoleGameStatus.WRONG_CHAT, event_id=event_id)
        if event["event_type"] != "find_mole":
            return FindMoleGameRun(
                FindMoleGameStatus.INVALID_SUSPECT,
                event_id=event_id,
            )
        now_value = _iso(now)
        if event["status"] == "expired" or event["expires_at"] <= now_value:
            if event["status"] == "active":
                self.lifecycle.expire_row(connection, event, now_value)
            return FindMoleGameRun(FindMoleGameStatus.EXPIRED, event_id=event_id)
        if event["status"] != "active":
            return FindMoleGameRun(
                FindMoleGameStatus.ALREADY_RESOLVED,
                event_id=event_id,
            )

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        existing = connection.execute(
            """
            SELECT id FROM find_mole_game_runs
            WHERE event_id = ? AND user_id = ?
            """,
            (event_id, user_id),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO find_mole_game_runs(
                    id, event_id, chat_id, message_id, user_id, token_hash,
                    status, started_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    run_id,
                    event_id,
                    chat_id,
                    event["message_id"] or 0,
                    user_id,
                    token_hash,
                    now_value,
                    event["expires_at"],
                ),
            )
            current_run_id = run_id
        else:
            current_run_id = existing["id"]
        row = self._find_mole_game_row_by_id(connection, current_run_id)
        return self._resolve_find_mole_accusation(
            connection,
            row,
            suspect_id,
            row["revision"],
            idempotency_key,
            now,
        )

    def _resolve_find_mole_accusation(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        suspect_id: str,
        revision: int,
        idempotency_key: str,
        now: datetime,
    ) -> FindMoleGameRun:
        current_status = self._find_mole_game_status(row)
        if current_status in {FindMoleGameStatus.WON, FindMoleGameStatus.FAILED}:
            return self._find_mole_game_run(row, current_status)

        now_value = _iso(now)
        if row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value:
            connection.execute(
                """
                UPDATE find_mole_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._find_mole_game_run(row, FindMoleGameStatus.EXPIRED)
        if row["event_status"] != "active":
            connection.execute(
                """
                UPDATE find_mole_game_runs
                SET status = 'lost_race', completed_at = ?
                WHERE id = ? AND status = 'ready'
                """,
                (now_value, row["id"]),
            )
            return self._find_mole_game_run(
                row,
                FindMoleGameStatus.ALREADY_RESOLVED,
            )
        public_case = json.loads(row["public_case_json"])
        suspect_ids = {suspect["id"] for suspect in public_case["suspects"]}
        if suspect_id not in suspect_ids:
            return self._find_mole_game_run(row, FindMoleGameStatus.INVALID_SUSPECT)
        if revision != row["revision"]:
            return self._find_mole_game_run(row, FindMoleGameStatus.STALE)

        correct = suspect_id == row["solution_suspect_id"]
        run_status = "failed"
        result_status = FindMoleGameStatus.FAILED
        item_reward = None
        agent_reward = None
        if correct:
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
                result_status = FindMoleGameStatus.ALREADY_RESOLVED
            else:
                item_id = self.settings.mole_reward_item_pool[
                    self.rng.randint(0, len(self.settings.mole_reward_item_pool) - 1)
                ]
                item_reward = DropReward(
                    "item",
                    item_id,
                    self.settings.mole_reward_item_amount,
                )
                agent_reward = Reward(
                    self.settings.mole_reward_agent_type,
                    self.settings.mole_reward_agent_amount,
                )
                self.economy.add_drop_reward(connection, row["user_id"], item_reward)
                self.economy.add_reward(connection, row["user_id"], agent_reward)
                self.lifecycle.advance_story(
                    connection,
                    row["chat_id"],
                    "find_mole",
                    now_value,
                )
                run_status = "won"
                result_status = FindMoleGameStatus.WON

        connection.execute(
            """
            UPDATE find_mole_game_runs
            SET status = ?, revision = revision + 1,
                selected_suspect_id = ?, accusation_key = ?,
                item_reward_id = ?, item_reward_amount = ?,
                agent_reward_type = ?, agent_reward_amount = ?, completed_at = ?
            WHERE id = ? AND status = 'ready'
            """,
            (
                run_status,
                suspect_id,
                idempotency_key,
                item_reward.reward_id if item_reward else None,
                item_reward.amount if item_reward else None,
                agent_reward.agent_type if agent_reward else None,
                agent_reward.amount if agent_reward else None,
                now_value,
                row["id"],
            ),
        )
        metadata = json.dumps(
            {
                "run_id": row["id"],
                "suspect_id": suspect_id,
                "revision": revision,
                "agent_reward": (
                    {
                        "agent_type": agent_reward.agent_type,
                        "amount": agent_reward.amount,
                    }
                    if agent_reward
                    else None
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'find_mole', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"find-mole-game:{row['id']}",
                row["event_id"],
                row["chat_id"],
                row["user_id"],
                result_status.value,
                item_reward.reward_type if item_reward else None,
                item_reward.reward_id if item_reward else None,
                item_reward.amount if item_reward else None,
                metadata,
                now_value,
            ),
        )
        updated = self._find_mole_game_row_by_id(connection, row["id"])
        return self._find_mole_game_run(
            updated,
            result_status,
            newly_won=result_status is FindMoleGameStatus.WON,
        )

    def _find_mole_game_row(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
    ) -> sqlite3.Row | None:
        return self._find_mole_game_row_where(
            connection, "r.token_hash = ?", token_hash
        )

    def _find_mole_game_row_by_id(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> sqlite3.Row | None:
        return self._find_mole_game_row_where(connection, "r.id = ?", run_id)

    @staticmethod
    def _find_mole_game_row_where(
        connection: sqlite3.Connection,
        predicate: str,
        value: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"""
            SELECT r.id, r.event_id, r.chat_id, r.message_id, r.user_id,
                   r.status AS run_status, r.revision, r.selected_suspect_id,
                   r.item_reward_id, r.item_reward_amount,
                   r.agent_reward_type, r.agent_reward_amount,
                   r.expires_at AS run_expires_at,
                   e.status AS event_status,
                   e.expires_at AS event_expires_at,
                   cse.public_case_json, cse.solution_suspect_id,
                   u.username
            FROM find_mole_game_runs r
            JOIN game_events e ON e.id = r.event_id
            JOIN find_mole_cases cse ON cse.event_id = r.event_id
            JOIN users u ON u.user_id = r.user_id
            WHERE {predicate}
            """,
            (value,),
        ).fetchone()

    @staticmethod
    def _find_mole_game_status(row: sqlite3.Row) -> FindMoleGameStatus:
        if row["run_status"] == "won":
            return FindMoleGameStatus.WON
        if row["run_status"] == "failed":
            return FindMoleGameStatus.FAILED
        if row["run_status"] == "expired":
            return FindMoleGameStatus.EXPIRED
        if row["run_status"] == "lost_race" or row["event_status"] != "active":
            return FindMoleGameStatus.ALREADY_RESOLVED
        return FindMoleGameStatus.READY

    @staticmethod
    def _find_mole_game_run(
        row: sqlite3.Row,
        status: FindMoleGameStatus,
        *,
        newly_won: bool = False,
    ) -> FindMoleGameRun:
        public_case = json.loads(row["public_case_json"])
        item_reward = (
            DropReward("item", row["item_reward_id"], row["item_reward_amount"])
            if row["item_reward_id"] is not None
            else None
        )
        agent_reward = (
            Reward(row["agent_reward_type"], row["agent_reward_amount"])
            if row["agent_reward_type"] is not None
            else None
        )
        return FindMoleGameRun(
            status,
            newly_won=newly_won,
            run_id=row["id"],
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            public_name=EconomyRepository.user_label(row["username"], None),
            title=public_case["title"],
            briefing=public_case["briefing"],
            clues=tuple(public_case["clues"]),
            suspects=tuple(
                MoleSuspectCard(
                    suspect["id"],
                    suspect["codename"],
                    suspect["role"],
                    suspect["dossier"],
                )
                for suspect in public_case["suspects"]
            ),
            revision=row["revision"],
            selected_suspect_id=row["selected_suspect_id"],
            expires_at=_datetime(row["run_expires_at"]),
            item_reward=item_reward,
            agent_reward=agent_reward,
        )
