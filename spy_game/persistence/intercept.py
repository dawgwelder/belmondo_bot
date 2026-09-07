"""Intercept persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from ..models import (
    DropReward,
    InterceptResult,
    InterceptGameRun,
    InterceptGameStatus,
    InterceptStatus,
)
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class InterceptRepository(RepositoryComponent):
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

    def answer_intercept(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        choice_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> InterceptResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return InterceptResult(InterceptStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return InterceptResult(InterceptStatus.WRONG_CHAT, event_id)
        if event["event_type"] != "intercept":
            return InterceptResult(InterceptStatus.INVALID_CHOICE, event_id)
        if event["status"] == "expired":
            return InterceptResult(InterceptStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return InterceptResult(
                InterceptStatus.ALREADY_RESOLVED,
                event_id,
                winner_user_id=event["winner_user_id"],
            )

        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return InterceptResult(InterceptStatus.EXPIRED, event_id)
        try:
            payload = json.loads(event["payload_json"])
        except json.JSONDecodeError:
            return InterceptResult(InterceptStatus.INVALID_CHOICE, event_id)
        scenario = self.settings.intercept_scenario(payload.get("config_id", ""))
        if scenario is None or choice_id not in {
            option.id for option in scenario.options
        }:
            return InterceptResult(InterceptStatus.INVALID_CHOICE, event_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'intercept'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            return InterceptResult(InterceptStatus.ALREADY_RESOLVED, event_id)

        correct = choice_id == scenario.correct_option_id
        reward = None
        if correct:
            reward = DropReward(
                "item",
                scenario.reward_item,
                scenario.reward_amount,
            )
            connection.execute(
                """
                INSERT INTO user_items(user_id, item_type, amount)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_type) DO UPDATE SET
                    amount = amount + excluded.amount
                """,
                (user_id, reward.reward_id, reward.amount),
            )
        metadata = json.dumps(
            {
                "config_id": scenario.id,
                "choice_id": choice_id,
                "correct_option_id": scenario.correct_option_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'intercept', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"intercept:{event_id}",
                event_id,
                chat_id,
                user_id,
                "correct" if correct else "incorrect",
                reward.reward_type if reward else None,
                reward.reward_id if reward else None,
                reward.amount if reward else None,
                metadata,
                now_value,
            ),
        )
        if correct:
            self.lifecycle.advance_story(connection, chat_id, "intercept", now_value)
        return InterceptResult(
            InterceptStatus.CORRECT if correct else InterceptStatus.INCORRECT,
            event_id,
            reward=reward,
            winner_user_id=user_id,
        )

    def start_intercept_game(
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
        targets: tuple[int, ...],
        now: datetime,
    ) -> InterceptGameRun:
        event = connection.execute(
            """
            SELECT e.*
            FROM game_events e
            JOIN chat_state c ON c.chat_id = e.chat_id
            WHERE e.chat_id = ? AND e.message_id = ?
              AND e.event_type = 'intercept' AND c.enabled = 1
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (chat_id, message_id),
        ).fetchone()
        if event is None:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if event["status"] == "expired" or event["expires_at"] <= now_value:
            if event["status"] == "active":
                self.lifecycle.expire_row(connection, event, now_value)
            return InterceptGameRun(
                InterceptGameStatus.EXPIRED,
                event_id=event["id"],
            )
        if event["status"] != "active":
            return InterceptGameRun(
                InterceptGameStatus.ALREADY_RESOLVED,
                event_id=event["id"],
            )
        try:
            payload = json.loads(event["payload_json"])
        except json.JSONDecodeError:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        scenario = self.settings.intercept_scenario(payload.get("config_id", ""))
        if scenario is None:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        existing = connection.execute(
            """
            SELECT * FROM intercept_game_runs
            WHERE event_id = ? AND user_id = ?
            """,
            (event["id"], user_id),
        ).fetchone()
        if existing is not None:
            if existing["status"] != "ready":
                return InterceptGameRun(
                    InterceptGameStatus.ALREADY_PLAYED,
                    run_id=existing["id"],
                    event_id=event["id"],
                    score=existing["score"],
                )
            if existing["expires_at"] <= now_value:
                connection.execute(
                    """
                    UPDATE intercept_game_runs SET status = 'expired'
                    WHERE id = ? AND status = 'ready'
                    """,
                    (existing["id"],),
                )
                return InterceptGameRun(
                    InterceptGameStatus.EXPIRED,
                    run_id=existing["id"],
                    event_id=event["id"],
                )
            connection.execute(
                "UPDATE intercept_game_runs SET token_hash = ? WHERE id = ?",
                (token_hash, existing["id"]),
            )
            return self._intercept_game_run(
                existing,
                self._format_intercept_game_prompt(scenario.prompt),
                InterceptGameStatus.READY,
                launch_token=None,
            )

        run_expires_at = min(
            now + timedelta(seconds=self.settings.intercept_game_run_seconds),
            _datetime(event["expires_at"]),
        )
        connection.execute(
            """
            INSERT INTO intercept_game_runs(
                id, event_id, chat_id, message_id, user_id, token_hash,
                targets_json, status, started_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
            """,
            (
                run_id,
                event["id"],
                chat_id,
                message_id,
                user_id,
                token_hash,
                json.dumps(targets, separators=(",", ":")),
                now_value,
                _iso(run_expires_at),
            ),
        )
        row = connection.execute(
            "SELECT * FROM intercept_game_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        return self._intercept_game_run(
            row,
            self._format_intercept_game_prompt(scenario.prompt),
            InterceptGameStatus.READY,
            launch_token=None,
        )

    def get_intercept_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        now: datetime,
    ) -> InterceptGameRun:
        row = self._intercept_game_row(connection, token_hash)
        if row is None:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        now_value = _iso(now)
        if row["run_status"] == "ready" and (
            row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value
        ):
            connection.execute(
                """
                UPDATE intercept_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._intercept_game_run(
                row,
                self._intercept_prompt(row["event_payload_json"]),
                InterceptGameStatus.EXPIRED,
            )
        status = self._intercept_game_status(row)
        return self._intercept_game_run(
            row,
            self._intercept_prompt(row["event_payload_json"]),
            status,
        )

    def finish_intercept_game(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
        locks: tuple[int, ...],
        now: datetime,
    ) -> InterceptGameRun:
        row = self._intercept_game_row(connection, token_hash)
        if row is None:
            return InterceptGameRun(InterceptGameStatus.NOT_FOUND)
        prompt = self._intercept_prompt(row["event_payload_json"])
        current_status = self._intercept_game_status(row)
        if current_status in {
            InterceptGameStatus.WON,
            InterceptGameStatus.FAILED,
        }:
            return self._intercept_game_run(row, prompt, current_status)

        now_value = _iso(now)
        if row["run_expires_at"] <= now_value or row["event_expires_at"] <= now_value:
            connection.execute(
                """
                UPDATE intercept_game_runs SET status = 'expired'
                WHERE id = ? AND status = 'ready'
                """,
                (row["id"],),
            )
            return self._intercept_game_run(
                row,
                prompt,
                InterceptGameStatus.EXPIRED,
            )
        if row["event_status"] != "active":
            connection.execute(
                """
                UPDATE intercept_game_runs
                SET status = 'lost_race', completed_at = ?
                WHERE id = ? AND status = 'ready'
                """,
                (now_value, row["id"]),
            )
            return self._intercept_game_run(
                row,
                prompt,
                InterceptGameStatus.ALREADY_RESOLVED,
            )

        targets = tuple(json.loads(row["targets_json"]))
        score = sum(
            max(0, 1000 - abs(target - locked) * 100)
            for target, locked in zip(targets, locks)
        )
        won = score >= self.settings.intercept_game_success_score
        run_status = "failed"
        result_status = InterceptGameStatus.FAILED
        reward = None
        if won:
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
                result_status = InterceptGameStatus.ALREADY_RESOLVED
            else:
                scenario = self.settings.intercept_scenario(
                    json.loads(row["event_payload_json"]).get("config_id", "")
                )
                reward = DropReward(
                    "item",
                    scenario.reward_item,
                    scenario.reward_amount,
                )
                self.economy.add_drop_reward(connection, row["user_id"], reward)
                self.lifecycle.advance_story(
                    connection,
                    row["chat_id"],
                    "intercept",
                    now_value,
                )
                run_status = "won"
                result_status = InterceptGameStatus.WON

        connection.execute(
            """
            UPDATE intercept_game_runs
            SET status = ?, score = ?, completed_at = ?
            WHERE id = ? AND status = 'ready'
            """,
            (run_status, score, now_value, row["id"]),
        )
        metadata = json.dumps(
            {"run_id": row["id"], "score": score, "locks": locks},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'intercept', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"intercept-game:{row['id']}",
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
        return InterceptGameRun(
            result_status,
            run_id=row["id"],
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            public_name=self.economy.user_label(row["username"], None),
            prompt=prompt,
            targets=targets,
            expires_at=_datetime(row["run_expires_at"]),
            success_score=self.settings.intercept_game_success_score,
            score=score,
            reward=reward,
        )

    def _intercept_game_row(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT r.id, r.event_id, r.chat_id, r.message_id, r.user_id,
                   r.targets_json, r.status AS run_status, r.score,
                   r.expires_at AS run_expires_at,
                   e.status AS event_status,
                   e.expires_at AS event_expires_at,
                   e.payload_json AS event_payload_json,
                   u.username
            FROM intercept_game_runs r
            JOIN game_events e ON e.id = r.event_id
            JOIN users u ON u.user_id = r.user_id
            WHERE r.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

    def _intercept_prompt(self, raw_payload: str) -> str | None:
        try:
            scenario_id = json.loads(raw_payload).get("config_id", "")
        except json.JSONDecodeError:
            return None
        scenario = self.settings.intercept_scenario(scenario_id)
        return self._format_intercept_game_prompt(scenario.prompt) if scenario else None

    def _format_intercept_game_prompt(self, prompt: str) -> str:
        return (
            f"{prompt} Настройте приёмник и восстановите "
            f"{self.settings.intercept_game_rounds} фрагментов передачи."
        )

    @staticmethod
    def _intercept_game_status(row: sqlite3.Row) -> InterceptGameStatus:
        if row["run_status"] == "won":
            return InterceptGameStatus.WON
        if row["run_status"] == "failed":
            return InterceptGameStatus.FAILED
        if row["run_status"] in {"lost_race", "expired"}:
            return (
                InterceptGameStatus.EXPIRED
                if row["run_status"] == "expired"
                else InterceptGameStatus.ALREADY_RESOLVED
            )
        if row["event_status"] != "active":
            return InterceptGameStatus.ALREADY_RESOLVED
        return InterceptGameStatus.READY

    def _intercept_game_run(
        self,
        row: sqlite3.Row,
        prompt: str | None,
        status: InterceptGameStatus,
        launch_token: str | None = None,
    ) -> InterceptGameRun:
        targets = tuple(json.loads(row["targets_json"]))
        reward = None
        if status is InterceptGameStatus.WON:
            scenario_id = (
                json.loads(row["event_payload_json"]).get(
                    "config_id",
                    "",
                )
                if "event_payload_json" in row.keys()
                else ""
            )
            scenario = self.settings.intercept_scenario(scenario_id)
            if scenario is not None:
                reward = DropReward(
                    "item",
                    scenario.reward_item,
                    scenario.reward_amount,
                )
        return InterceptGameRun(
            status,
            run_id=row["id"],
            launch_token=launch_token,
            event_id=row["event_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            public_name=(
                self.economy.user_label(row["username"], None)
                if "username" in row.keys()
                else None
            ),
            prompt=prompt,
            targets=targets,
            expires_at=_datetime(
                row["run_expires_at"]
                if "run_expires_at" in row.keys()
                else row["expires_at"]
            ),
            success_score=self.settings.intercept_game_success_score,
            score=row["score"],
            reward=reward,
        )
