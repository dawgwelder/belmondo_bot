"""SQLite persistence operations for Spy Clicker."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone

from .models import (
    AdminResult,
    AgencyResult,
    AgencyStatus,
    AgentCost,
    AgentHolding,
    ChatStatus,
    ChaseResult,
    ChaseStatus,
    ClaimResult,
    ClaimStatus,
    CooperativeResult,
    CooperativeStatus,
    DeadDropGameRun,
    DeadDropGameStatus,
    DeadDropGuess,
    DeadDropResult,
    DeathOperationResult,
    DeathOperationStatus,
    DirectorState,
    DropReward,
    EconomyStatus,
    EquipmentResult,
    EquipmentStatus,
    ExchangeResult,
    EquippedItem,
    ExpiredEvent,
    Inventory,
    InterceptResult,
    InterceptGameRun,
    InterceptGameStatus,
    InterceptStatus,
    ItemCost,
    ItemHolding,
    LeaderboardEntry,
    NpcResult,
    NpcStatus,
    PrestigeResult,
    PreparedTick,
    Profile,
    RecruitmentProgress,
    Reward,
    SpawnEvent,
)
from .director import DirectorDecision
from .rewards import RewardResolver
from .scheduler import ActivityPolicy, RandomSource
from .settings import AGENT_TYPES, ITEM_TYPES, SpySettings

logger = logging.getLogger("Belmondo Logger")


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
            self._expire_row(connection, row, now_value)
            expired.append(ExpiredEvent(row["id"], row["chat_id"], row["message_id"]))

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
                            item.event_type for item in self.settings.event_weights
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
                "manual": manual,
            }
        elif event_type == "death_operation":
            payload_data = {
                "action": "death",
                "config_id": "all_in_v1",
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
            npc_id = self.settings.npc_ids[
                self.rng.randint(0, len(self.settings.npc_ids) - 1)
            ]
            payload_data = {
                "action": "npc_exchange",
                "config_id": npc_id,
                "recipe_ids": [
                    recipe.id for recipe in self.settings.npc_recipes_for(npc_id)
                ],
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
            self._expire_row(connection, event, now_value)
            return ClaimResult(ClaimStatus.EXPIRED, event_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
        if self._item_is_equipped(connection, user_id, "wiretap"):
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
            self._expire_row(connection, event, now_value)
            return DeadDropResult(ClaimStatus.EXPIRED, event_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
            self._add_reward(
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
                self._expire_row(connection, event, now_value)
            return DeadDropGameRun(
                DeadDropGameStatus.EXPIRED,
                event_id=event["id"],
            )
        if event["status"] != "active":
            return DeadDropGameRun(
                DeadDropGameStatus.ALREADY_RESOLVED,
                event_id=event["id"],
            )

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
                self._add_drop_reward(connection, row["user_id"], reward)
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
            public_name=self._user_label(row["username"], None),
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
            public_name=self._user_label(row["username"], None),
            code_length=self.settings.dead_drop_game_code_length,
            attempts=self._dead_drop_attempts(row["attempts_json"]),
            expires_at=_datetime(row["run_expires_at"]),
            reward=reward,
        )

    def run_death_operation(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> DeathOperationResult:
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return DeathOperationResult(DeathOperationStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return DeathOperationResult(DeathOperationStatus.WRONG_CHAT, event_id)
        if event["event_type"] != "death_operation":
            return DeathOperationResult(
                DeathOperationStatus.INVALID_ACTION,
                event_id,
            )
        if event["status"] == "expired":
            return DeathOperationResult(DeathOperationStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return DeathOperationResult(
                DeathOperationStatus.ALREADY_RESOLVED,
                event_id,
                winner_user_id=event["winner_user_id"],
            )

        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self._expire_row(connection, event, now_value)
            return DeathOperationResult(DeathOperationStatus.EXPIRED, event_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
        staked = self.get_agents(connection, user_id)
        if not staked:
            return DeathOperationResult(
                DeathOperationStatus.INSUFFICIENT_AGENTS,
                event_id,
            )

        stake_payload = [
            {"agent_type": holding.agent_type, "amount": holding.amount}
            for holding in staked
        ]
        participant = connection.execute(
            """
            SELECT status, payload_json FROM event_participants
            WHERE event_id = ? AND user_id = ?
            """,
            (event_id, user_id),
        ).fetchone()
        pending_is_current = False
        if participant is not None and participant["status"] == "pending":
            try:
                pending_payload = json.loads(participant["payload_json"])
                confirmation_expires_at = _datetime(
                    pending_payload.get("confirmation_expires_at")
                )
                pending_is_current = (
                    confirmation_expires_at is not None
                    and confirmation_expires_at > now
                    and pending_payload.get("staked") == stake_payload
                )
            except (AttributeError, TypeError, json.JSONDecodeError, ValueError):
                pending_is_current = False

        if not pending_is_current:
            confirmation_expires_at = min(
                now
                + timedelta(seconds=self.settings.death_operation_confirmation_seconds),
                _datetime(event["expires_at"]),
            )
            payload = json.dumps(
                {
                    "staked": stake_payload,
                    "confirmation_expires_at": _iso(confirmation_expires_at),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO event_participants(
                    event_id, user_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(event_id, user_id) DO UPDATE SET
                    status = 'pending',
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (event_id, user_id, payload, now_value, now_value),
            )
            return DeathOperationResult(
                DeathOperationStatus.CONFIRMATION_REQUIRED,
                event_id,
                staked=staked,
                confirmation_expires_at=confirmation_expires_at,
            )

        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'death_operation'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            current = connection.execute(
                "SELECT status, winner_user_id FROM game_events WHERE id = ?",
                (event_id,),
            ).fetchone()
            status = (
                DeathOperationStatus.EXPIRED
                if current["status"] == "expired"
                else DeathOperationStatus.ALREADY_RESOLVED
            )
            return DeathOperationResult(
                status,
                event_id,
                winner_user_id=current["winner_user_id"],
            )

        connection.execute(
            "UPDATE user_agents SET amount = 0 WHERE user_id = ? AND amount > 0",
            (user_id,),
        )
        roll = self.rng.randint(1, 100)
        won = roll <= self.settings.death_operation_success_percent
        rewards: list[Reward] = []
        if won:
            for holding in staked:
                reward = Reward(
                    holding.agent_type,
                    holding.amount * self.settings.death_operation_reward_multiplier,
                )
                self._add_reward(connection, user_id, reward)
                rewards.append(reward)
            bonus_index = self.rng.randint(
                0,
                len(self.settings.death_operation_bonus_pool) - 1,
            )
            bonus = Reward(self.settings.death_operation_bonus_pool[bonus_index], 1)
            self._add_reward(connection, user_id, bonus)
            rewards.append(bonus)

        outcome = "won" if won else "lost"
        metadata_data = {
            "config_id": "all_in_v1",
            "success_percent": self.settings.death_operation_success_percent,
            "roll": roll,
            "staked": stake_payload,
            "rewards": [
                {"agent_type": reward.agent_type, "amount": reward.amount}
                for reward in rewards
            ],
        }
        metadata = json.dumps(
            metadata_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO event_history(
                idempotency_key, event_id, chat_id, user_id, event_type,
                outcome, reward_type, reward_id, reward_amount,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, 'death_operation', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"death-operation:{event_id}",
                event_id,
                chat_id,
                user_id,
                outcome,
                "agent_bundle" if won else None,
                (
                    f"all_agents_x{self.settings.death_operation_reward_multiplier}"
                    "+tier3"
                    if won
                    else None
                ),
                sum(reward.amount for reward in rewards) if won else None,
                metadata,
                now_value,
            ),
        )
        connection.execute(
            """
            UPDATE event_participants
            SET status = 'resolved', payload_json = ?, updated_at = ?
            WHERE event_id = ? AND user_id = ?
            """,
            (metadata, now_value, event_id, user_id),
        )
        return DeathOperationResult(
            DeathOperationStatus.WON if won else DeathOperationStatus.LOST,
            event_id,
            staked=staked,
            rewards=tuple(rewards),
            winner_user_id=user_id,
        )

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
            self._expire_row(connection, event, now_value)
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

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
            self._advance_story(connection, chat_id, "intercept", now_value)
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
                self._expire_row(connection, event, now_value)
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

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
                self._add_drop_reward(connection, row["user_id"], reward)
                self._advance_story(
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
            public_name=self._user_label(row["username"], None),
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
                self._user_label(row["username"], None)
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
            self._expire_row(connection, event, now_value)
            return CooperativeResult(
                CooperativeStatus.EXPIRED,
                event_id,
                required_contributions=required,
            )

        self._ensure_user(connection, user_id, username, display_name, now_value)
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
            self._add_reward(connection, participant_id, reward)
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
        self._advance_story(
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
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None or event["event_type"] != "chase":
            return ChaseResult(ChaseStatus.NOT_FOUND, event_id)
        if event["chat_id"] != chat_id:
            return ChaseResult(ChaseStatus.WRONG_CHAT, event_id)
        if event["status"] == "expired":
            return ChaseResult(ChaseStatus.EXPIRED, event_id)
        if event["status"] != "active":
            return ChaseResult(ChaseStatus.ALREADY_RESOLVED, event_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self._expire_row(connection, event, now_value)
            return ChaseResult(ChaseStatus.EXPIRED, event_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
        starter = connection.execute(
            """
            SELECT p.user_id, u.username, u.display_name
            FROM event_participants p
            JOIN users u ON u.user_id = p.user_id
            WHERE p.event_id = ? AND p.status = 'pending'
            ORDER BY p.created_at, p.user_id LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if starter is None:
            connection.execute(
                """
                INSERT INTO event_participants(
                    event_id, user_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', '{"stage":1}', ?, ?)
                """,
                (event_id, user_id, now_value, now_value),
            )
            connection.execute(
                """
                INSERT INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, created_at
                ) VALUES (?, ?, ?, ?, 'chase', 'started', ?)
                """,
                (f"chase-start:{event_id}", event_id, chat_id, user_id, now_value),
            )
            return ChaseResult(
                ChaseStatus.STARTED,
                event_id,
                starter_user_id=user_id,
                starter_name=self._user_label(username, display_name),
            )

        starter_user_id = starter["user_id"]
        starter_name = self._user_label(
            starter["username"],
            starter["display_name"],
        )
        interceptor_name = self._user_label(username, display_name)
        resolved = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'chase'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if resolved.rowcount != 1:
            return ChaseResult(ChaseStatus.ALREADY_RESOLVED, event_id)
        connection.execute(
            """
            UPDATE event_participants
            SET status = 'resolved', payload_json = '{"stage":2}', updated_at = ?
            WHERE event_id = ? AND user_id = ? AND status = 'pending'
            """,
            (now_value, event_id, starter_user_id),
        )
        starter_reward = Reward(
            self.settings.chase_starter_reward.agent_type,
            self.settings.chase_starter_reward.amount,
        )
        interceptor_reward = Reward(
            self.settings.chase_interceptor_reward.agent_type,
            self.settings.chase_interceptor_reward.amount,
        )
        for participant_id, role, reward in (
            (starter_user_id, "starter", starter_reward),
            (user_id, "interceptor", interceptor_reward),
        ):
            self._add_reward(connection, participant_id, reward)
            connection.execute(
                """
                INSERT INTO event_history(
                    idempotency_key, event_id, chat_id, user_id, event_type,
                    outcome, reward_type, reward_id, reward_amount,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'chase', 'rewarded', 'agent', ?, ?, ?, ?)
                """,
                (
                    f"chase-reward:{event_id}:{role}",
                    event_id,
                    chat_id,
                    participant_id,
                    reward.agent_type,
                    reward.amount,
                    json.dumps({"role": role}, separators=(",", ":")),
                    now_value,
                ),
            )
        return ChaseResult(
            status=ChaseStatus.COMPLETED,
            event_id=event_id,
            starter_user_id=starter_user_id,
            interceptor_user_id=user_id,
            starter_reward=starter_reward,
            interceptor_reward=interceptor_reward,
            starter_name=starter_name,
            interceptor_name=interceptor_name,
        )

    def exchange_with_handler(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> ExchangeResult:
        recipe = self.settings.handler_recipe(recipe_id)
        if recipe is None:
            return ExchangeResult(EconomyStatus.INVALID_RECIPE, event_id, recipe_id)
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return ExchangeResult(EconomyStatus.NOT_FOUND, event_id, recipe_id)
        if event["chat_id"] != chat_id:
            return ExchangeResult(EconomyStatus.WRONG_CHAT, event_id, recipe_id)
        if event["event_type"] != "handler":
            return ExchangeResult(EconomyStatus.INVALID_RECIPE, event_id, recipe_id)
        if event["status"] == "expired":
            return ExchangeResult(EconomyStatus.EXPIRED, event_id, recipe_id)
        if event["status"] != "active":
            return ExchangeResult(EconomyStatus.ALREADY_RESOLVED, event_id, recipe_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self._expire_row(connection, event, now_value)
            return ExchangeResult(EconomyStatus.EXPIRED, event_id, recipe_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
        if not self._has_costs(connection, user_id, recipe.costs):
            return ExchangeResult(
                EconomyStatus.INSUFFICIENT_RESOURCES,
                event_id,
                recipe_id,
                required=recipe.costs,
            )
        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'handler'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            return ExchangeResult(EconomyStatus.ALREADY_RESOLVED, event_id, recipe_id)

        reward = self.reward_resolver.resolve_exchange(recipe, self.rng)
        self._spend_costs(connection, user_id, recipe.costs)
        self._add_reward(connection, user_id, reward)
        metadata = json.dumps(
            {
                "recipe_id": recipe.id,
                "costs": {cost.agent_type: cost.amount for cost in recipe.costs},
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
            ) VALUES (?, ?, ?, ?, 'handler', 'exchanged', 'agent', ?, ?, ?, ?)
            """,
            (
                f"exchange:{event_id}",
                event_id,
                chat_id,
                user_id,
                reward.agent_type,
                reward.amount,
                metadata,
                now_value,
            ),
        )
        connection.execute(
            """
            INSERT INTO economy_history(
                idempotency_key, user_id, action, source_event_id,
                recipe_id, metadata_json, created_at
            ) VALUES (?, ?, 'exchange', ?, ?, ?, ?)
            """,
            (
                f"exchange:{event_id}",
                user_id,
                event_id,
                recipe.id,
                metadata,
                now_value,
            ),
        )
        self._advance_story(connection, chat_id, "handler", now_value)
        return ExchangeResult(
            EconomyStatus.SUCCESS,
            event_id,
            recipe_id,
            reward=reward,
            required=recipe.costs,
        )

    def interact_with_npc(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> NpcResult:
        recipe = self.settings.npc_recipe(recipe_id)
        if recipe is None:
            return NpcResult(NpcStatus.INVALID_RECIPE, event_id, recipe_id)
        event = connection.execute(
            "SELECT * FROM game_events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return NpcResult(NpcStatus.NOT_FOUND, event_id, recipe_id)
        if event["chat_id"] != chat_id:
            return NpcResult(NpcStatus.WRONG_CHAT, event_id, recipe_id)
        if event["event_type"] != "npc":
            return NpcResult(NpcStatus.INVALID_RECIPE, event_id, recipe_id)
        payload = json.loads(event["payload_json"])
        if payload.get("config_id") != recipe.npc_id or recipe.id not in payload.get(
            "recipe_ids", ()
        ):
            return NpcResult(NpcStatus.INVALID_RECIPE, event_id, recipe_id)
        if event["status"] == "expired":
            return NpcResult(NpcStatus.EXPIRED, event_id, recipe_id)
        if event["status"] != "active":
            return NpcResult(NpcStatus.ALREADY_RESOLVED, event_id, recipe_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self._expire_row(connection, event, now_value)
            return NpcResult(NpcStatus.EXPIRED, event_id, recipe_id)

        self._ensure_user(connection, user_id, username, display_name, now_value)
        if not self._has_costs(connection, user_id, recipe.agent_costs) or not (
            self._has_item_costs(connection, user_id, recipe.item_costs)
        ):
            return NpcResult(
                NpcStatus.INSUFFICIENT_RESOURCES,
                event_id,
                recipe_id,
                required_agents=recipe.agent_costs,
                required_items=recipe.item_costs,
            )
        claimed = connection.execute(
            """
            UPDATE game_events
            SET status = 'resolved', winner_user_id = ?, resolved_at = ?
            WHERE id = ? AND chat_id = ? AND event_type = 'npc'
              AND status = 'active' AND expires_at > ?
            """,
            (user_id, now_value, event_id, chat_id, now_value),
        )
        if claimed.rowcount != 1:
            return NpcResult(NpcStatus.ALREADY_RESOLVED, event_id, recipe_id)

        agency_level = connection.execute(
            "SELECT agency_level FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        self._spend_costs(connection, user_id, recipe.agent_costs)
        self._spend_item_costs(connection, user_id, recipe.item_costs)
        reward = self.reward_resolver.resolve_npc(recipe, agency_level, self.rng)
        self._add_drop_reward(connection, user_id, reward)
        metadata = json.dumps(
            {
                "npc_id": recipe.npc_id,
                "recipe_id": recipe.id,
                "agent_costs": {
                    cost.agent_type: cost.amount for cost in recipe.agent_costs
                },
                "item_costs": {
                    cost.item_type: cost.amount for cost in recipe.item_costs
                },
                "agency_level": agency_level,
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
            ) VALUES (?, ?, ?, ?, 'npc', 'exchanged', ?, ?, ?, ?, ?)
            """,
            (
                f"npc:{event_id}",
                event_id,
                chat_id,
                user_id,
                reward.reward_type,
                reward.reward_id,
                reward.amount,
                metadata,
                now_value,
            ),
        )
        self._advance_story(connection, chat_id, "npc", now_value)
        return NpcResult(
            NpcStatus.SUCCESS,
            event_id,
            recipe_id,
            reward=reward,
            required_agents=recipe.agent_costs,
            required_items=recipe.item_costs,
        )

    def increase_reputation(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_reputation: int,
        now: datetime,
    ) -> PrestigeResult:
        now_value = _iso(now)
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return PrestigeResult(EconomyStatus.DISABLED, expected_reputation)
        self._ensure_user(connection, user_id, username, display_name, now_value)
        current = connection.execute(
            "SELECT reputation FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        if current != expected_reputation:
            return PrestigeResult(EconomyStatus.STALE, current)
        required = self.settings.prestige_costs(current)
        if not self._has_costs(connection, user_id, required):
            return PrestigeResult(
                EconomyStatus.INSUFFICIENT_RESOURCES,
                current,
                required=required,
            )
        self._spend_costs(connection, user_id, required)
        updated = connection.execute(
            """
            UPDATE users SET reputation = reputation + 1, updated_at = ?
            WHERE user_id = ? AND reputation = ?
            """,
            (now_value, user_id, current),
        )
        if updated.rowcount != 1:
            raise RuntimeError("reputation changed inside serialized transaction")
        metadata = json.dumps(
            {
                "from": current,
                "to": current + 1,
                "costs": {cost.agent_type: cost.amount for cost in required},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO economy_history(
                idempotency_key, user_id, action, recipe_id,
                metadata_json, created_at
            ) VALUES (?, ?, 'prestige', 'reputation', ?, ?)
            """,
            (
                f"prestige:{user_id}:{current}",
                user_id,
                metadata,
                now_value,
            ),
        )
        return PrestigeResult(EconomyStatus.SUCCESS, current + 1, required)

    def found_agency(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        expected_agency_level: int,
        now: datetime,
    ) -> AgencyResult:
        now_value = _iso(now)
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        required_reputation = self.settings.agency_reputation_requirement(
            expected_agency_level
        )
        required_agents = self.settings.agency_requirements(expected_agency_level)
        if chat is None or not chat["enabled"]:
            return AgencyResult(
                AgencyStatus.DISABLED,
                expected_agency_level,
                required_reputation,
                required_agents,
            )
        self._ensure_user(connection, user_id, username, display_name, now_value)
        user = connection.execute(
            "SELECT reputation, agency_level FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        current_level = user["agency_level"]
        if current_level != expected_agency_level:
            return AgencyResult(
                AgencyStatus.STALE,
                current_level,
                self.settings.agency_reputation_requirement(current_level),
                self.settings.agency_requirements(current_level),
            )
        if current_level >= self.settings.agency_max_level:
            return AgencyResult(
                AgencyStatus.MAX_LEVEL,
                current_level,
                required_reputation,
                required_agents,
            )
        if user["reputation"] < required_reputation or not self._has_costs(
            connection,
            user_id,
            required_agents,
        ):
            return AgencyResult(
                AgencyStatus.INSUFFICIENT_RESOURCES,
                current_level,
                required_reputation,
                required_agents,
            )

        self._spend_costs(connection, user_id, required_agents)
        updated = connection.execute(
            """
            UPDATE users
            SET agency_level = agency_level + 1, reputation = 0, updated_at = ?
            WHERE user_id = ? AND agency_level = ? AND reputation >= ?
            """,
            (now_value, user_id, current_level, required_reputation),
        )
        if updated.rowcount != 1:
            raise RuntimeError("agency level changed inside serialized transaction")
        metadata = json.dumps(
            {
                "reputation_spent": user["reputation"],
                "agent_costs": {
                    cost.agent_type: cost.amount for cost in required_agents
                },
                "rare_bonus_percent": min(
                    (current_level + 1) * self.settings.agency_rare_bonus_percent,
                    self.settings.agency_max_level
                    * self.settings.agency_rare_bonus_percent,
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO agency_history(
                idempotency_key, user_id, from_level, to_level,
                metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"agency:{user_id}:{current_level}",
                user_id,
                current_level,
                current_level + 1,
                metadata,
                now_value,
            ),
        )
        return AgencyResult(
            AgencyStatus.SUCCESS,
            current_level + 1,
            self.settings.agency_reputation_requirement(current_level + 1),
            self.settings.agency_requirements(current_level + 1),
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
        self._ensure_user(connection, user_id, username, display_name, now_value)
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

    def get_inventory(
        self,
        connection: sqlite3.Connection,
        user_id: int,
    ) -> Inventory:
        item_rows = connection.execute(
            """
            SELECT item_type, amount FROM user_items
            WHERE user_id = ? AND amount > 0
            ORDER BY item_type
            """,
            (user_id,),
        ).fetchall()
        equipped_rows = connection.execute(
            """
            SELECT slot, item_type FROM equipped_items
            WHERE user_id = ?
            ORDER BY slot
            """,
            (user_id,),
        ).fetchall()
        items = tuple(
            ItemHolding(row["item_type"], row["amount"])
            for row in item_rows
            if row["item_type"] in ITEM_TYPES
        )
        equipped = tuple(
            EquippedItem(row["slot"], row["item_type"])
            for row in equipped_rows
            if row["item_type"] in ITEM_TYPES
        )
        return Inventory(items, equipped, self.settings.equipment_slots)

    def get_leaderboard(
        self,
        connection: sqlite3.Connection,
        limit: int,
    ) -> tuple[LeaderboardEntry, ...]:
        rare_agents = tuple(
            agent_id for agent_id, agent in AGENT_TYPES.items() if agent.tier >= 3
        )
        placeholders = ",".join("?" for _ in rare_agents)
        rows = connection.execute(
            f"""
            SELECT
                u.user_id,
                u.username,
                u.reputation,
                u.agency_level,
                COALESCE(SUM(a.amount), 0) AS total_agents,
                COALESCE(SUM(
                    CASE WHEN a.agent_type IN ({placeholders})
                         THEN a.amount ELSE 0 END
                ), 0) AS rare_agents
            FROM users u
            LEFT JOIN user_agents a ON a.user_id = u.user_id
            GROUP BY u.user_id
            ORDER BY u.agency_level DESC, u.reputation DESC,
                     rare_agents DESC, total_agents DESC, u.user_id
            LIMIT ?
            """,
            (*rare_agents, limit),
        ).fetchall()
        return tuple(
            LeaderboardEntry(
                rank=index,
                user_id=row["user_id"],
                display_name=self._user_label(row["username"], None),
                total_agents=row["total_agents"],
                rare_agents=row["rare_agents"],
                reputation=row["reputation"],
                agency_level=row["agency_level"],
            )
            for index, row in enumerate(rows, start=1)
        )

    def equip_item(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        item_type: str,
    ) -> EquipmentResult:
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return EquipmentResult(EquipmentStatus.DISABLED, item_type)
        item = ITEM_TYPES.get(item_type)
        if item is None or item.category.value != "equipment":
            return EquipmentResult(EquipmentStatus.NOT_EQUIPMENT, item_type)
        owned = connection.execute(
            """
            SELECT amount FROM user_items
            WHERE user_id = ? AND item_type = ?
            """,
            (user_id, item_type),
        ).fetchone()
        if owned is None or owned["amount"] <= 0:
            return EquipmentResult(EquipmentStatus.NOT_OWNED, item_type)
        existing = connection.execute(
            """
            SELECT slot FROM equipped_items
            WHERE user_id = ? AND item_type = ?
            """,
            (user_id, item_type),
        ).fetchone()
        if existing is not None:
            return EquipmentResult(
                EquipmentStatus.ALREADY_EQUIPPED,
                item_type,
                existing["slot"],
            )
        occupied = {
            row["slot"]
            for row in connection.execute(
                "SELECT slot FROM equipped_items WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
        slot = next(
            (
                candidate
                for candidate in range(1, self.settings.equipment_slots + 1)
                if candidate not in occupied
            ),
            None,
        )
        if slot is None:
            return EquipmentResult(EquipmentStatus.NO_FREE_SLOT, item_type)
        connection.execute(
            """
            INSERT INTO equipped_items(user_id, slot, item_type)
            VALUES (?, ?, ?)
            """,
            (user_id, slot, item_type),
        )
        return EquipmentResult(EquipmentStatus.SUCCESS, item_type, slot)

    def unequip_item(
        self,
        connection: sqlite3.Connection,
        chat_id: int,
        user_id: int,
        slot: int,
    ) -> EquipmentResult:
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return EquipmentResult(EquipmentStatus.DISABLED, slot=slot)
        if slot <= 0:
            return EquipmentResult(EquipmentStatus.INVALID_SLOT, slot=slot)
        row = connection.execute(
            """
            SELECT item_type FROM equipped_items
            WHERE user_id = ? AND slot = ?
            """,
            (user_id, slot),
        ).fetchone()
        if row is None:
            return EquipmentResult(EquipmentStatus.NOT_EQUIPPED, slot=slot)
        connection.execute(
            "DELETE FROM equipped_items WHERE user_id = ? AND slot = ?",
            (user_id, slot),
        )
        return EquipmentResult(EquipmentStatus.SUCCESS, row["item_type"], slot)

    @staticmethod
    def _item_is_equipped(
        connection: sqlite3.Connection,
        user_id: int,
        item_type: str,
    ) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM equipped_items
                WHERE user_id = ? AND item_type = ?
                """,
                (user_id, item_type),
            ).fetchone()
            is not None
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

    @staticmethod
    def _user_label(username: str | None, _display_name: str | None) -> str:
        if username and username.strip("@"):
            return f"@{username.lstrip('@')}"
        return "Скрытый агент"

    @staticmethod
    def _has_costs(
        connection: sqlite3.Connection,
        user_id: int,
        costs: tuple[AgentCost, ...],
    ) -> bool:
        holdings = {
            row["agent_type"]: row["amount"]
            for row in connection.execute(
                "SELECT agent_type, amount FROM user_agents WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
        return all(holdings.get(cost.agent_type, 0) >= cost.amount for cost in costs)

    @staticmethod
    def _spend_costs(
        connection: sqlite3.Connection,
        user_id: int,
        costs: tuple[AgentCost, ...],
    ) -> None:
        for cost in costs:
            cursor = connection.execute(
                """
                UPDATE user_agents SET amount = amount - ?
                WHERE user_id = ? AND agent_type = ? AND amount >= ?
                """,
                (cost.amount, user_id, cost.agent_type, cost.amount),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    "agent balance changed inside serialized transaction"
                )

    @staticmethod
    def _has_item_costs(
        connection: sqlite3.Connection,
        user_id: int,
        costs: tuple[ItemCost, ...],
    ) -> bool:
        holdings = {
            row["item_type"]: row["amount"]
            for row in connection.execute(
                "SELECT item_type, amount FROM user_items WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }
        return all(holdings.get(cost.item_type, 0) >= cost.amount for cost in costs)

    @staticmethod
    def _spend_item_costs(
        connection: sqlite3.Connection,
        user_id: int,
        costs: tuple[ItemCost, ...],
    ) -> None:
        for cost in costs:
            cursor = connection.execute(
                """
                UPDATE user_items SET amount = amount - ?
                WHERE user_id = ? AND item_type = ? AND amount >= ?
                """,
                (cost.amount, user_id, cost.item_type, cost.amount),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("item balance changed inside serialized transaction")

    @staticmethod
    def _add_reward(
        connection: sqlite3.Connection,
        user_id: int,
        reward: Reward,
    ) -> None:
        connection.execute(
            """
            INSERT INTO user_agents(user_id, agent_type, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, agent_type) DO UPDATE SET
                amount = amount + excluded.amount
            """,
            (user_id, reward.agent_type, reward.amount),
        )

    @classmethod
    def _add_drop_reward(
        cls,
        connection: sqlite3.Connection,
        user_id: int,
        reward: DropReward,
    ) -> None:
        if reward.reward_type == "agent":
            cls._add_reward(
                connection,
                user_id,
                Reward(reward.reward_id, reward.amount),
            )
        elif reward.reward_type == "item":
            connection.execute(
                """
                INSERT INTO user_items(user_id, item_type, amount)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, item_type) DO UPDATE SET
                    amount = amount + excluded.amount
                """,
                (user_id, reward.reward_id, reward.amount),
            )

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
            return (
                payload.get("action") == "exchange"
                and payload.get("recipe_ids")
                == [recipe.id for recipe in self.settings.handler_recipes]
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "death_operation":
            return (
                payload.get("action") == "death"
                and payload.get("config_id") == "all_in_v1"
                and isinstance(payload.get("manual"), bool)
            )
        if event_type == "intercept":
            return (
                payload.get("action") == "answer"
                and self.settings.intercept_scenario(payload.get("config_id", ""))
                is not None
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
            return (
                payload.get("action") == "npc_exchange"
                and npc_id in self.settings.npc_ids
                and payload.get("recipe_ids")
                == [recipe.id for recipe in self.settings.npc_recipes_for(npc_id)]
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
    def _advance_story(
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
