"""Atomic ownership, escrow and replayable actions for both Death modes."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import death_mission as engine
from .models import Reward
from .settings import AGENT_TYPES


TERMINAL = {
    "won",
    "lost",
    "extracted",
    "timed_out",
    "cancelled_refunded",
    "expired",
    "lost_race",
}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def iso(now):
    if now.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return now.astimezone(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class DeathMissionRun:
    payload: dict
    launch_token: str | None = None

    @property
    def status(self):
        return self.payload["status"]


class DeathMissionRepository:
    def __init__(self, repository):
        self.repo = repository

    @staticmethod
    def row(connection, *, token_hash=None, run_id=None):
        return connection.execute(
            "SELECT r.*, e.chat_id, e.message_id, e.status AS event_status, "
            "e.expires_at AS event_expires_at FROM death_mission_runs r "
            "JOIN game_events e ON e.id = r.event_id WHERE "
            + ("r.token_hash = ?" if token_hash else "r.id = ?"),
            (token_hash or run_id,),
        ).fetchone()

    def stake(self, connection, user_id):
        return {
            a.agent_type: a.amount for a in self.repo.get_agents(connection, user_id)
        }

    @staticmethod
    def tactics(connection, user_id):
        counts = dict(
            connection.execute(
                "SELECT achievement, COUNT(*) FROM death_mission_achievements "
                "WHERE user_id = ? GROUP BY achievement",
                (user_id,),
            ).fetchall()
        )
        unlocked = ["balanced"]
        if counts.get("checkpoint", 0) >= 3:
            unlocked.append("stealth")
        if counts.get("raid", 0):
            unlocked.append("assault")
        return unlocked, counts

    def view(self, connection, row, error=None):
        if row is None:
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "not_found"}
            )
        unlocked, progress = self.tactics(connection, row["user_id"])
        state = json.loads(row["state_json"])
        stake = json.loads(row["stake_json"])
        payload = {
            "game_type": "death_operation",
            "status": row["status"],
            "revision": row["revision"],
            "mode": row["mode"],
            "tactic": row["tactic"],
            "bonus": row["bonus"],
            "expires_at": row["expires_at"],
            "stake": self.bundle(stake),
            "extraction": self.bundle({k: v // 2 for k, v in stake.items() if v // 2}),
            "rules": json.loads(row["rules_json"]),
            "mission": engine.public_state(state),
            "result": json.loads(row["result_json"]),
            "tactics": [{"id": k, "name": engine.TACTICS[k][0]} for k in unlocked],
            "progress": progress,
        }
        if error:
            payload["error"] = error
        return DeathMissionRun(payload)

    @staticmethod
    def bundle(holdings):
        return [
            dict(
                id=k,
                name=AGENT_TYPES[k].display_name,
                emoji=AGENT_TYPES[k].emoji,
                amount=v,
            )
            for k, v in sorted(holdings.items())
            if v > 0
        ]

    def start(
        self,
        connection,
        *,
        chat_id,
        message_id,
        user_id,
        username,
        display_name,
        token_hash,
        now,
    ):
        event = connection.execute(
            "SELECT * FROM game_events WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ).fetchone()
        if not event or event["event_type"] != "death_operation":
            return self.view(connection, None)
        existing = connection.execute(
            "SELECT id FROM death_mission_runs WHERE event_id = ? AND user_id = ?",
            (event["id"], user_id),
        ).fetchone()
        if existing:
            row = self.refresh(
                connection, self.row(connection, run_id=existing["id"]), now
            )
            connection.execute(
                "UPDATE death_mission_runs SET token_hash = ? WHERE id = ?",
                (token_hash, row["id"]),
            )
            return self.view(connection, row)
        if (
            event["status"] != "active"
            or event["expires_at"] <= iso(now)
            or json.loads(event["payload_json"]).get("config_id") != "death_choice_v1"
        ):
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "expired"}
            )
        owner = connection.execute(
            "SELECT 1 FROM death_mission_runs WHERE event_id = ? AND committed_at IS NOT NULL",
            (event["id"],),
        ).fetchone()
        if owner:
            return DeathMissionRun(
                {"game_type": "death_operation", "status": "lost_race"}
            )
        self.repo._ensure_user(connection, user_id, username, display_name, iso(now))
        settings = self.repo.settings
        rules = dict(
            version=engine.VERSION,
            all_in_percent=settings.death_operation_success_percent,
            multiplier=settings.death_operation_reward_multiplier,
            seconds=settings.death_mission_seconds,
            tier3=list(settings.death_operation_bonus_pool),
            tier4=list(settings.death_mission_tier4_pool),
            confirm_seconds=settings.death_operation_confirmation_seconds,
        )
        run_id = secrets.token_hex(12)
        connection.execute(
            "INSERT INTO death_mission_runs(id,event_id,user_id,token_hash,status,stake_json,"
            "rules_json,expires_at) VALUES(?,?,?,?,'preview',?,?,?)",
            (
                run_id,
                event["id"],
                user_id,
                token_hash,
                dump(self.stake(connection, user_id)),
                dump(rules),
                event["expires_at"],
            ),
        )
        return self.view(connection, self.row(connection, run_id=run_id))

    def refresh(self, connection, row, now):
        if row is None or row["status"] in TERMINAL:
            return row
        if row["status"] == "in_run":
            try:
                if row["mode"] == "mission":
                    engine.validate(json.loads(row["state_json"]))
            except (ValueError, TypeError, KeyError, IndexError):
                self.settle(connection, row, "cancelled_refunded", now)
                return self.row(connection, run_id=row["id"])
            if row["event_status"] != "active":
                self.settle(connection, row, "cancelled_refunded", now)
            elif row["expires_at"] <= iso(now):
                self.settle(connection, row, "timed_out", now)
        elif row["event_status"] != "active" or row["expires_at"] <= iso(now):
            status = "expired" if row["expires_at"] <= iso(now) else "lost_race"
            connection.execute(
                "UPDATE death_mission_runs SET status=?, revision=revision+1 WHERE id=?",
                (status, row["id"]),
            )
        return self.row(connection, run_id=row["id"])

    def get(self, connection, token_hash, now):
        return self.view(
            connection,
            self.refresh(connection, self.row(connection, token_hash=token_hash), now),
        )

    def mutate(
        self, connection, *, token_hash, action, revision, operation_id, choice, now
    ):
        row = self.row(connection, token_hash=token_hash)
        if row is None:
            return self.view(connection, None)
        request_hash = hashlib.sha256(
            dump([action, revision, choice]).encode()
        ).hexdigest()
        previous = connection.execute(
            "SELECT * FROM death_mission_actions WHERE run_id=? AND operation_id=?",
            (row["id"], operation_id),
        ).fetchone()
        if previous:
            if previous["request_hash"] != request_hash:
                return self.view(connection, row, "IDEMPOTENCY_CONFLICT")
            return DeathMissionRun(json.loads(previous["response_json"]))
        row = self.refresh(connection, row, now)
        error = None
        if row["status"] in TERMINAL:
            error = "ALREADY_FINISHED"
        elif row["revision"] != revision:
            error = "STALE_REVISION"
        else:
            error = self.apply(connection, row, action, choice, now)
        result = self.view(connection, self.row(connection, run_id=row["id"]), error)
        connection.execute(
            "INSERT INTO death_mission_actions VALUES(?,?,?,?,?)",
            (row["id"], operation_id, request_hash, dump(result.payload), iso(now)),
        )
        return result

    def apply(self, connection, row, action, choice, now):
        status = row["status"]
        if action == "arm" and status in {"preview", "armed"}:
            if choice.get("mode") not in {"all_in", "mission"}:
                return "INVALID_ACTION"
            tactic = choice.get("tactic", "balanced")
            bonus = choice.get("bonus", "tier3")
            if tactic not in self.tactics(connection, row["user_id"])[
                0
            ] or bonus not in {"tier3", "tier4"}:
                return "INVALID_ACTION"
            stake = self.stake(connection, row["user_id"])
            if not stake:
                return "INSUFFICIENT_AGENTS"
            if stake != json.loads(row["stake_json"]):
                connection.execute(
                    "UPDATE death_mission_runs SET status='preview', stake_json=?, revision=revision+1 WHERE id=?",
                    (dump(stake), row["id"]),
                )
                return "STALE_STAKE"
            connection.execute(
                "UPDATE death_mission_runs SET status='armed', mode=?, tactic=?, bonus=?, armed_at=?, "
                "revision=revision+1 WHERE id=?",
                (choice["mode"], tactic, bonus, iso(now), row["id"]),
            )
            return None
        if action == "back" and status == "armed":
            connection.execute(
                "UPDATE death_mission_runs SET status='preview', armed_at=NULL, "
                "revision=revision+1 WHERE id=?",
                (row["id"],),
            )
            return None
        if action == "commit" and status == "armed":
            return self.commit(connection, row, now)
        if status != "in_run":
            return "INVALID_ACTION"
        state = json.loads(row["state_json"])
        if action == "extract":
            if not state["checkpoint"]:
                return "EXTRACTION_LOCKED"
            self.settle(connection, row, "extracted", now)
            return None
        if action == "abandon":
            self.settle(connection, row, "lost", now)
            return None
        if action != "action" or not isinstance(choice.get("id"), str):
            return "INVALID_ACTION"
        try:
            state = engine.advance(state, choice["id"], row["seed"])
        except ValueError:
            return "INVALID_ACTION"
        connection.execute(
            "UPDATE death_mission_runs SET state_json=?, revision=revision+1 WHERE id=?",
            (dump(state), row["id"]),
        )
        if state["outcome"]:
            self.settle(
                connection,
                self.row(connection, run_id=row["id"]),
                state["outcome"],
                now,
            )
        return None

    def commit(self, connection, row, now):
        rules = json.loads(row["rules_json"])
        if (
            datetime.fromisoformat(row["armed_at"])
            + timedelta(seconds=rules["confirm_seconds"])
            <= now
        ):
            connection.execute(
                "UPDATE death_mission_runs SET status='preview', revision=revision+1 WHERE id=?",
                (row["id"],),
            )
            return "CONFIRMATION_EXPIRED"
        if connection.execute(
            "SELECT 1 FROM death_mission_runs WHERE user_id=? AND status='in_run'",
            (row["user_id"],),
        ).fetchone():
            return "RUN_IN_PROGRESS"
        if connection.execute(
            "SELECT 1 FROM death_mission_runs WHERE event_id=? AND committed_at IS NOT NULL",
            (row["event_id"],),
        ).fetchone():
            return "LOST_RACE"
        stake = self.stake(connection, row["user_id"])
        if not stake or stake != json.loads(row["stake_json"]):
            connection.execute(
                "UPDATE death_mission_runs SET status='preview', stake_json=?, "
                "revision=revision+1 WHERE id=?",
                (dump(stake), row["id"]),
            )
            return "STALE_STAKE"
        seed = secrets.token_hex(32)
        state = engine.initial(seed, row["tactic"]) if row["mode"] == "mission" else {}
        expires = iso(now + timedelta(seconds=rules["seconds"]))
        connection.execute(
            "UPDATE death_mission_runs SET status='in_run', seed=?, state_json=?, committed_at=?, "
            "expires_at=?, revision=revision+1 WHERE id=?",
            (seed, dump(state), iso(now), expires, row["id"]),
        )
        connection.execute(
            "UPDATE game_events SET expires_at=? WHERE id=? AND status='active'",
            (expires, row["event_id"]),
        )
        for agent_type, amount in stake.items():
            connection.execute(
                "UPDATE user_agents SET amount=amount-? WHERE user_id=? AND agent_type=?",
                (amount, row["user_id"], agent_type),
            )
            connection.execute(
                "INSERT INTO death_mission_stakes VALUES(?,?,?)",
                (row["id"], agent_type, amount),
            )
        connection.execute(
            "INSERT INTO death_mission_ledger VALUES(?,'reserve',?,?)",
            (row["id"], dump(stake), iso(now)),
        )
        connection.execute(
            "UPDATE death_mission_runs SET status='lost_race', revision=revision+1 "
            "WHERE event_id=? AND id!=? AND status IN ('preview','armed')",
            (row["event_id"], row["id"]),
        )
        connection.execute(
            "INSERT INTO event_participants(event_id,user_id,status,payload_json,created_at,updated_at) "
            "VALUES(?,?,'pending','{}',?,?) ON CONFLICT(event_id,user_id) DO UPDATE SET status='pending'",
            (row["event_id"], row["user_id"], iso(now), iso(now)),
        )
        if row["mode"] == "all_in":
            outcome = (
                "won"
                if engine.roll(seed, "all_in") < rules["all_in_percent"]
                else "lost"
            )
            self.settle(
                connection, self.row(connection, run_id=row["id"]), outcome, now
            )
        return None

    def settle(self, connection, row, outcome, now):
        if row["status"] != "in_run":
            return
        rules = json.loads(row["rules_json"])
        state = {} if outcome == "cancelled_refunded" else json.loads(row["state_json"])
        stake = dict(
            connection.execute(
                "SELECT agent_type,amount FROM death_mission_stakes WHERE run_id=?",
                (row["id"],),
            ).fetchall()
        )
        if outcome == "won":
            returned = {k: v * rules["multiplier"] for k, v in stake.items()}
        elif outcome == "cancelled_refunded":
            returned = stake.copy()
        elif outcome == "extracted" or (
            outcome == "timed_out" and state.get("checkpoint")
        ):
            returned = {k: v // 2 for k, v in stake.items() if v // 2}
        else:
            returned = {}
        bonus = {}
        if outcome == "won":
            tier = row["bonus"] if row["mode"] == "mission" else "tier3"
            pool = rules[tier]
            agent = pool[engine.roll(row["seed"], "bonus", len(pool))]
            bonus[agent] = 2 if row["mode"] == "mission" and tier == "tier3" else 1
        for bundle in (returned, bonus):
            for agent, amount in bundle.items():
                self.repo._add_reward(connection, row["user_id"], Reward(agent, amount))
        if row["mode"] == "mission" and outcome != "cancelled_refunded":
            achievements = []
            if state.get("node", 0) >= 3:
                achievements.append("checkpoint")
                if state.get("survived_raid"):
                    achievements.append("raid")
            if outcome == "won":
                achievements.append("won")
            for achievement in achievements:
                connection.execute(
                    "INSERT INTO death_mission_achievements VALUES(?,?,?)",
                    (row["user_id"], row["id"], achievement),
                )
        result = dict(
            outcome=outcome, returned=self.bundle(returned), bonus=self.bundle(bonus)
        )
        if state:
            state.update(outcome=outcome, phase="done")
        connection.execute(
            "UPDATE death_mission_runs SET status=?, result_json=?, state_json=?, "
            "completed_at=?, revision=revision+1 WHERE id=?",
            (outcome, dump(result), dump(state), iso(now), row["id"]),
        )
        connection.execute(
            "INSERT INTO death_mission_ledger VALUES(?,'settle',?,?)",
            (row["id"], dump(result), iso(now)),
        )
        connection.execute(
            "UPDATE game_events SET status='resolved', winner_user_id=?, resolved_at=? WHERE id=?",
            (row["user_id"], iso(now), row["event_id"]),
        )
        connection.execute(
            "UPDATE event_participants SET status='resolved', payload_json=?, updated_at=? "
            "WHERE event_id=? AND user_id=?",
            (dump(result), iso(now), row["event_id"], row["user_id"]),
        )
        metadata = dict(mode=row["mode"], rules=rules, stake=stake, result=result)
        connection.execute(
            "INSERT INTO event_history(idempotency_key,event_id,chat_id,user_id,event_type,outcome,metadata_json,created_at) "
            "VALUES(?,?,?,?,'death_operation',?,?,?)",
            (
                f"death-operation:{row['event_id']}",
                row["event_id"],
                row["chat_id"],
                row["user_id"],
                outcome,
                dump(metadata),
                iso(now),
            ),
        )
        connection.execute(
            "INSERT INTO death_mission_outbox(run_id) VALUES(?)", (row["id"],)
        )

    def finish_event(self, connection, event_id, now, *, refund=False):
        found = connection.execute(
            "SELECT id FROM death_mission_runs WHERE event_id=? AND status='in_run'",
            (event_id,),
        ).fetchone()
        if not found:
            return False
        row = self.row(connection, run_id=found["id"])
        if not refund:
            row = self.refresh(connection, row, now)
        if row["status"] == "in_run":
            self.settle(
                connection, row, "cancelled_refunded" if refund else "timed_out", now
            )
        return True

    def reconcile(self, connection, now):
        rows = connection.execute(
            "SELECT id FROM death_mission_runs WHERE status='in_run'"
        ).fetchall()
        for row in rows:
            self.refresh(connection, self.row(connection, run_id=row["id"]), now)

    def pending_results(self, connection):
        rows = connection.execute(
            "SELECT r.id, r.user_id, e.chat_id, e.message_id, u.username "
            "FROM death_mission_outbox o JOIN death_mission_runs r ON r.id=o.run_id "
            "JOIN game_events e ON e.id=r.event_id JOIN users u ON u.user_id=r.user_id "
            "WHERE o.delivered_at IS NULL AND (o.next_attempt_at IS NULL OR o.next_attempt_at <= ?) "
            "ORDER BY o.attempts, o.run_id LIMIT 20",
            (iso(datetime.now(timezone.utc)),),
        ).fetchall()
        return [
            dict(
                run_id=r["id"],
                chat_id=r["chat_id"],
                message_id=r["message_id"],
                username=r["username"],
                payload=self.view(
                    connection, self.row(connection, run_id=r["id"])
                ).payload,
            )
            for r in rows
        ]
