"""Lifecycle persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from ..models import AdminResult, ChatStatus, ExpiredEvent
import logging
from ..death_mission_repository import DeathMissionRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime

logger = logging.getLogger("Belmondo Logger")


class LifecycleRepository(RepositoryComponent):
    def __init__(
        self, context: RepositoryContext, *, death_mission: DeathMissionRepository
    ) -> None:
        super().__init__(context)
        self.death_mission = death_mission

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
            result_managed = self.expire_row(connection, row, now_value)
            expired_events.append(
                ExpiredEvent(
                    row["id"],
                    row["chat_id"],
                    row["message_id"],
                    row["event_type"],
                    result_managed,
                )
            )
        active_rows = connection.execute(
            """
            SELECT id, chat_id, event_type, message_id, payload_json
            FROM game_events
            WHERE status = 'active' AND expires_at > ?
            """,
            (now_value,),
        ).fetchall()
        for row in active_rows:
            if self._payload_is_valid(row["event_type"], row["payload_json"]):
                continue
            logger.warning(
                "spy_invalid_persisted_event event_id=%s chat_id=%s event_type=%s",
                row["id"],
                row["chat_id"],
                row["event_type"],
            )
            if self.death_mission.finish_event(connection, row["id"], now, refund=True):
                continue
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
                ) VALUES (?, ?, ?, ?, 'invalid_payload', ?)
                """,
                (
                    f"invalid-payload:{row['id']}",
                    row["id"],
                    row["chat_id"],
                    row["event_type"],
                    now_value,
                ),
            )
            expired_events.append(
                ExpiredEvent(
                    row["id"],
                    row["chat_id"],
                    row["message_id"],
                    row["event_type"],
                    cancelled=True,
                )
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
            if self.death_mission.finish_event(connection, row["id"], now, refund=True):
                continue
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
                chat_id, enabled, activity_score, activity_updated_at,
                activity_profile, updated_at
            ) VALUES (?, 1, 0, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = 1,
                updated_at = excluded.updated_at
            """,
            (
                chat_id,
                now_value,
                self.settings.default_activity_profile,
                now_value,
            ),
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
                chat_id, enabled, activity_score, activity_updated_at,
                activity_profile, updated_at
            ) VALUES (?, 0, 0, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                enabled = 0,
                next_event_at = NULL,
                updated_at = excluded.updated_at
            """,
            (
                chat_id,
                now_value,
                self.settings.default_activity_profile,
                now_value,
            ),
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
        if active is not None and self.death_mission.finish_event(
            connection, active["id"], now, refund=True
        ):
            return AdminResult(
                True,
                "Spy Clicker выключен. Ставка операции возвращена.",
                message_id_to_close=active["message_id"],
            )
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

    def html5_event_type(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        message_id: int,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT e.event_type
            FROM game_events e
            JOIN chat_state c ON c.chat_id = e.chat_id
            WHERE e.chat_id = ? AND e.message_id = ? AND c.enabled = 1
              AND e.event_type IN ('intercept', 'dead_drop', 'find_mole', 'death_operation')
            ORDER BY e.created_at DESC
            LIMIT 1
            """,
            (chat_id, message_id),
        ).fetchone()
        return row["event_type"] if row is not None else None

    def html5_game_type(
        self,
        connection: sqlite3.Connection,
        token_hash: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT game_type FROM (
                SELECT 'intercept' AS game_type, token_hash
                FROM intercept_game_runs
                UNION ALL
                SELECT 'dead_drop' AS game_type, token_hash
                FROM dead_drop_game_runs
                UNION ALL
                SELECT 'find_mole' AS game_type, token_hash
                FROM find_mole_game_runs
                UNION ALL
                SELECT 'death_operation' AS game_type, token_hash
                FROM death_mission_runs
            ) WHERE token_hash = ?
            LIMIT 1
            """,
            (token_hash,),
        ).fetchone()
        return row["game_type"] if row is not None else None

    def _payload_is_valid(self, event_type: str, raw_payload: str) -> bool:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            return False
        if not isinstance(payload, dict):
            return False
        if event_type == "recruitment":
            return (
                payload.get("action") == "claim"
                and payload.get("reward_pool") == "basic_recruitment"
                and payload.get(
                    "required_claims",
                    self.settings.recruitment_winner_count,
                )
                == self.settings.recruitment_winner_count
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "dead_drop":
            return (
                payload.get("action") == "search"
                and payload.get("reward_pool") == "basic_dead_drop"
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "handler":
            reward_multiplier = payload.get("reward_multiplier", 1)
            return (
                payload.get("action") == "exchange"
                and payload.get("recipe_ids")
                == [recipe.id for recipe in self.settings.handler_recipes]
                and type(reward_multiplier) is int
                and reward_multiplier
                in {1, self.settings.handler_event_reward_multiplier}
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "death_operation":
            return (
                payload.get("action") == "death"
                and payload.get("config_id") in {"all_in_v1", "death_choice_v1"}
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "intercept":
            return (
                payload.get("action") == "answer"
                and self.settings.intercept_scenario(payload.get("config_id", ""))
                is not None
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "find_mole":
            return (
                payload.get("action") == "accuse"
                and self.settings.mole_case(payload.get("config_id", "")) is not None
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "cooperative_operation":
            return (
                payload.get("action") == "contribute"
                and payload.get("config_id") == "network_sweep_v1"
                and payload.get("required_contributions")
                == self.settings.cooperative_required_contributions
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "chase":
            return (
                payload.get("action") == "chase"
                and payload.get("config_id") == "two_stage_v1"
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "npc":
            npc_id = payload.get("config_id")
            reward_multiplier = payload.get("reward_multiplier", 1)
            return (
                payload.get("action") == "npc_exchange"
                and npc_id in self.settings.npc_ids
                and payload.get("recipe_ids")
                == [recipe.id for recipe in self.settings.npc_recipes_for(npc_id)]
                and type(reward_multiplier) is int
                and reward_multiplier in {1, self.settings.npc_event_reward_multiplier}
                and isinstance(payload.get("manual"), bool)
            )
        return False

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
        summary = connection.execute(
            "SELECT summary FROM story_summary WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        return ChatStatus(
            chat_id=chat_id,
            enabled=bool(chat["enabled"]) if chat else False,
            activity_score=float(chat["activity_score"]) if chat else 0.0,
            next_event_at=_datetime(chat["next_event_at"]) if chat else None,
            active_event_id=active["id"] if active else None,
            active_event_expires_at=_datetime(active["expires_at"]) if active else None,
            activity_profile=(
                chat["activity_profile"]
                if chat
                else self.settings.default_activity_profile
            ),
            story_arc=chat["story_arc"] if chat else None,
            story_stage=chat["story_stage"] if chat else 0,
            story_summary=summary["summary"] if summary else None,
        )

    @staticmethod
    def advance_story(
        connection: sqlite3.Connection,
        chat_id: int,
        resolved_event_type: str,
        now_value: str,
    ) -> None:
        state = connection.execute(
            "SELECT story_arc, story_stage FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if state is None:
            return
        arc = state["story_arc"]
        stage = state["story_stage"]
        next_stage = None
        summary = None
        if resolved_event_type == "intercept" and arc is None:
            arc = "mole_hunt"
            next_stage = 1
            summary = (
                "Перехваченный шифр связал активность Секции 7 с исчезновением "
                "полковника Вяземского. Сеть начала поиск крота."
            )
        elif (
            resolved_event_type == "cooperative_operation"
            and arc == "mole_hunt"
            and stage == 1
        ):
            next_stage = 2
            summary = (
                "Участники совместно восстановили маршрут внедрения. След ведёт "
                "к куратору, владеющему архивом Секции 7."
            )
        elif resolved_event_type == "handler" and arc == "mole_hunt" and stage == 2:
            next_stage = 3
            summary = (
                "Куратор подтвердил происхождение архива. Ячейка крота раскрыта, "
                "но полковник Вяземский всё ещё не найден."
            )
        elif resolved_event_type == "find_mole" and arc == "mole_hunt" and stage == 3:
            next_stage = 4
            summary = (
                "Сеть сопоставила досье и раскрыла крота. Полковник Вяземский "
                "найден, архив Секции 7 сохранён."
            )
        elif resolved_event_type == "npc" and arc == "mole_hunt" and stage == 3:
            next_stage = 4
            summary = (
                "Контакт среди специальных кураторов вывел сеть на полковника "
                "Вяземского. Поиск крота завершён, архив Секции 7 сохранён."
            )
        if next_stage is None or summary is None:
            return
        connection.execute(
            """
            UPDATE chat_state SET story_arc = ?, story_stage = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (arc, next_stage, now_value, chat_id),
        )
        connection.execute(
            """
            INSERT INTO story_summary(chat_id, summary, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                summary = excluded.summary,
                updated_at = excluded.updated_at
            """,
            (chat_id, summary, now_value),
        )

    def expire_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now_value: str,
    ) -> bool:
        """Expire the event; return whether a personal mission owns its result message."""
        if self.death_mission.finish_event(connection, row["id"], _datetime(now_value)):
            return True
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
        return False
