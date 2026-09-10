"""Scheduling persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from ..models import AdminResult, DirectorState, ExpiredEvent, PreparedTick, SpawnEvent
from ..director import DirectorDecision
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso, _datetime


class SchedulingRepository(RepositoryComponent):
    def __init__(
        self, context: RepositoryContext, *, lifecycle: LifecycleRepository
    ) -> None:
        super().__init__(context)
        self.lifecycle = lifecycle

    def set_activity_profile(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        profile: str,
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
                activity_profile = excluded.activity_profile,
                updated_at = excluded.updated_at
            """,
            (chat_id, now_value, profile, now_value),
        )
        return AdminResult(True, f"Профиль активности переключён на {profile}.")

    def prepare_tick(
        self,
        connection: sqlite3.Connection,
        activity_counts: Mapping[int, int],
        allowed_chat_ids: frozenset[int],
        now: datetime,
    ) -> PreparedTick:
        now_value = _iso(now)
        # Trigger candidates are intentionally valid for one scheduler window only.
        # This also drops timers created by the former delayed scheduling policy.
        connection.execute(
            "UPDATE chat_state SET next_event_at = NULL "
            "WHERE next_event_at IS NOT NULL"
        )
        self._apply_activity(connection, activity_counts, now, now_value)
        trigger_reasons = self._arm_triggers(
            connection,
            activity_counts,
            allowed_chat_ids,
            now,
            now_value,
        )

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
            result_managed = self.lifecycle.expire_row(connection, row, now_value)
            expired.append(
                ExpiredEvent(
                    row["id"],
                    row["chat_id"],
                    row["message_id"],
                    row["event_type"],
                    result_managed,
                )
            )

        due: list[DirectorState] = []
        if allowed_chat_ids:
            placeholders = ",".join("?" for _ in allowed_chat_ids)
            due_rows = connection.execute(
                f"""
                SELECT chat_id, activity_score, last_event_at,
                       story_arc, story_stage
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
                recent = connection.execute(
                    """
                    SELECT event_type FROM game_events
                    WHERE chat_id = ?
                    ORDER BY created_at DESC LIMIT 5
                    """,
                    (row["chat_id"],),
                ).fetchall()
                last_event_at = _datetime(row["last_event_at"])
                minutes_since = (
                    max(0, int((now - last_event_at).total_seconds() // 60))
                    if last_event_at
                    else None
                )
                due.append(
                    DirectorState(
                        chat_id=row["chat_id"],
                        activity_score=float(row["activity_score"]),
                        active_players=activity_counts.get(row["chat_id"], 0),
                        minutes_since_last_event=minutes_since,
                        recent_events=tuple(item["event_type"] for item in recent),
                        story_arc=row["story_arc"],
                        story_stage=row["story_stage"],
                        allowed_events=tuple(
                            item.event_type
                            for item in self.settings.event_weights
                            if item.event_type != "find_mole"
                            or (
                                row["story_arc"] == "mole_hunt"
                                and row["story_stage"] >= 3
                            )
                        ),
                        trigger_reason=trigger_reasons[row["chat_id"]],
                    )
                )

        return PreparedTick(tuple(due), tuple(expired))

    def spawn_due(
        self,
        connection: sqlite3.Connection,
        state: DirectorState,
        now: datetime,
        decision: DirectorDecision,
    ) -> SpawnEvent | None:
        if decision.event_type not in state.allowed_events:
            raise ValueError("director selected a disallowed event")
        now_value = _iso(now)
        due = connection.execute(
            """
            SELECT 1 FROM chat_state
            WHERE chat_id = ? AND enabled = 1
              AND next_event_at IS NOT NULL AND next_event_at <= ?
              AND NOT EXISTS (
                  SELECT 1 FROM game_events
                  WHERE game_events.chat_id = chat_state.chat_id
                    AND game_events.status = 'active'
              )
            """,
            (state.chat_id, now_value),
        ).fetchone()
        if due is None:
            return None
        return self._insert_event(
            connection,
            state.chat_id,
            now,
            event_type=decision.event_type,
            manual=False,
            decision=decision,
            trigger_reason=state.trigger_reason,
        )

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
                SELECT activity_score, activity_updated_at
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
            connection.execute(
                """
                UPDATE chat_state
                SET activity_score = ?, activity_updated_at = ?,
                    updated_at = ?
                WHERE chat_id = ?
                """,
                (
                    score,
                    now_value,
                    now_value,
                    chat_id,
                ),
            )

    def _arm_triggers(
        self,
        connection: sqlite3.Connection,
        activity_counts: Mapping[int, int],
        allowed_chat_ids: frozenset[int],
        now: datetime,
        now_value: str,
    ) -> dict[int, str]:
        if not allowed_chat_ids:
            return {}
        placeholders = ",".join("?" for _ in allowed_chat_ids)
        rows = connection.execute(
            f"""
            SELECT chat_id, activity_score, activity_updated_at, last_event_at,
                   activity_profile
            FROM chat_state
            WHERE enabled = 1
              AND chat_id IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1 FROM game_events
                  WHERE game_events.chat_id = chat_state.chat_id
                    AND game_events.status = 'active'
                    AND game_events.expires_at > ?
              )
            ORDER BY chat_id
            """,
            (*sorted(allowed_chat_ids), now_value),
        ).fetchall()
        reasons: dict[int, str] = {}
        for row in rows:
            activity_updated_at = _datetime(row["activity_updated_at"])
            score = self.activity_policy.update_score(
                row["activity_score"],
                activity_updated_at,
                0,
                now,
            )
            reason = self.activity_policy.trigger_reason(
                score,
                activity_counts.get(row["chat_id"], 0),
                activity_updated_at,
                _datetime(row["last_event_at"]),
                now,
                self.rng,
                profile=row["activity_profile"],
            )
            if reason is None:
                continue
            reasons[row["chat_id"]] = reason
            connection.execute(
                """
                UPDATE chat_state
                SET activity_score = ?, activity_updated_at = ?,
                    next_event_at = ?, updated_at = ?
                WHERE chat_id = ?
                """,
                (score, now_value, now_value, now_value, row["chat_id"]),
            )
        return reasons

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
        decision: DirectorDecision | None = None,
        trigger_reason: str = "manual",
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
        mole_case = None
        if event_type == "recruitment":
            payload_data = {
                "action": "claim",
                "reward_pool": "basic_recruitment",
                "required_claims": self.settings.recruitment_winner_count,
                "manual": manual,
            }
        elif event_type == "dead_drop":
            payload_data = {
                "action": "search",
                "reward_pool": "basic_dead_drop",
                "manual": manual,
            }
        elif event_type == "handler":
            payload_data = {
                "action": "exchange",
                "recipe_ids": [recipe.id for recipe in self.settings.handler_recipes],
                "reward_multiplier": self.settings.handler_event_reward_multiplier,
                "manual": manual,
            }
        elif event_type == "death_operation":
            payload_data = {
                "action": "death",
                "config_id": "death_choice_v1"
                if self.settings.death_mission_enabled
                else "all_in_v1",
                "manual": manual,
            }
        elif event_type == "intercept":
            scenario = self.settings.intercept_scenarios[
                self.rng.randint(0, len(self.settings.intercept_scenarios) - 1)
            ]
            payload_data = {
                "action": "answer",
                "config_id": scenario.id,
                "manual": manual,
            }
        elif event_type == "find_mole":
            mole_case = self.settings.mole_cases[
                self.rng.randint(0, len(self.settings.mole_cases) - 1)
            ]
            expires_at = now + timedelta(seconds=self.settings.mole_game_run_seconds)
            payload_data = {
                "action": "accuse",
                "config_id": mole_case.id,
                "manual": manual,
            }
        elif event_type == "cooperative_operation":
            payload_data = {
                "action": "contribute",
                "config_id": "network_sweep_v1",
                "required_contributions": (
                    self.settings.cooperative_required_contributions
                ),
                "manual": manual,
            }
        elif event_type == "chase":
            payload_data = {
                "action": "chase",
                "config_id": "two_stage_v1",
                "manual": manual,
            }
        elif event_type == "npc":
            npc_id = self.settings.event_npc_ids[
                self.rng.randint(0, len(self.settings.event_npc_ids) - 1)
            ]
            payload_data = {
                "action": "npc_exchange",
                "config_id": npc_id,
                "recipe_ids": [
                    recipe.id for recipe in self.settings.npc_recipes_for(npc_id)
                ],
                "reward_multiplier": self.settings.npc_event_reward_multiplier,
                "manual": manual,
            }
        else:
            raise ValueError(f"unsupported event type: {event_type}")
        payload_data["tone"] = decision.tone if decision else "bureaucratic"
        payload_data["story_hook"] = decision.story_hook if decision else None
        payload_data["intensity"] = decision.intensity if decision else 1
        payload_data["trigger_reason"] = trigger_reason
        lore_context = ()
        if payload_data["story_hook"]:
            lore_rows = connection.execute(
                """
                SELECT DISTINCT l.name, l.text
                FROM lore l
                LEFT JOIN lore_tags t ON t.lore_id = l.id
                WHERE l.id = ? OR t.tag = ?
                ORDER BY l.id
                LIMIT 3
                """,
                (payload_data["story_hook"], payload_data["story_hook"]),
            ).fetchall()
            lore_context = tuple(f"{row['name']}: {row['text']}" for row in lore_rows)
        payload = json.dumps(
            payload_data,
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
        if mole_case is not None:
            public_case = json.dumps(
                {
                    "title": mole_case.title,
                    "briefing": mole_case.briefing,
                    "clues": mole_case.clues,
                    "suspects": [
                        {
                            "id": suspect.id,
                            "codename": suspect.codename,
                            "role": suspect.role,
                            "dossier": suspect.dossier,
                        }
                        for suspect in mole_case.suspects
                    ],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO find_mole_cases(
                    event_id, public_case_json, solution_suspect_id,
                    template_id, template_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    public_case,
                    mole_case.correct_suspect_id,
                    mole_case.id,
                    mole_case.version,
                    _iso(now),
                ),
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
        return SpawnEvent(
            event_id=event_id,
            chat_id=chat_id,
            event_type=event_type,
            expires_at=expires_at,
            config_id=payload_data.get("config_id"),
            tone=payload_data["tone"],
            story_hook=payload_data["story_hook"],
            lore_context=lore_context,
            trigger_reason=trigger_reason,
        )
