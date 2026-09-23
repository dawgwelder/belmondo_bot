"""Persistent rehearsal of the real engine, without stakes or economic writes."""

import hashlib
import json
import secrets
from datetime import timedelta

from . import death_mission as engine
from .death_mission_repository import DeathMissionRun, TERMINAL, dump, iso


class DeathMissionPractice:
    def __init__(self, missions):
        self.missions = missions

    @staticmethod
    def row(connection, token_hash):
        return connection.execute("SELECT * FROM death_mission_practice WHERE token_hash=?", (token_hash,)).fetchone()

    def refresh(self, connection, row, now):
        if row is None or row["status"] in TERMINAL:
            return row
        status = None
        if not self.missions.available(connection, row):
            status = "cancelled_refunded"
        elif row["expires_at"] <= iso(now):
            status = "timed_out"
        try:
            state = json.loads(row["state_json"])
            engine.validate(state)
        except (TypeError, ValueError, KeyError, IndexError):
            state, status = {}, "cancelled_refunded"
        if status:
            if state:
                state.update(phase="done", outcome=status)
            connection.execute(
                "UPDATE death_mission_practice SET status=?,state_json=?,revision=revision+1 WHERE id=?",
                (status, dump(state), row["id"]),
            )
        return self.row(connection, row["token_hash"])

    def view(self, row, error=None):
        if row is None:
            return DeathMissionRun(dict(game_type="death_operation", status="not_found"))
        state = json.loads(row["state_json"])
        payload = dict(
            game_type="death_operation",
            practice=True,
            status=row["status"],
            revision=row["revision"],
            expires_at=row["expires_at"],
            mode="mission",
            tactic=state.get("tactic", "balanced"),
            bonus="none",
            stake=[],
            extraction=[],
            victory=[],
            bonuses=[],
            rules=dict(version=state.get("version", engine.DEFAULT_VERSION), multiplier=0, seconds=900),
            mission=engine.public_state(state),
            result={},
            tactics=[],
            progress={},
        )
        if row["status"] in TERMINAL:
            payload["result"] = dict(outcome=row["status"], returned=[], bonus=[])
        if error:
            payload["error"] = error
        return DeathMissionRun(payload)

    def start(self, connection, *, user_id, chat_id, username, display_name, token_hash, now):
        if not self.missions.available(connection, {"chat_id": chat_id}):
            return DeathMissionRun(dict(game_type="death_operation", status="disabled"))
        self.missions.economy.ensure_user(connection, user_id, username, display_name, iso(now))
        row = connection.execute(
            "SELECT * FROM death_mission_practice WHERE user_id=? AND status='in_run'", (user_id,)
        ).fetchone()
        row = self.refresh(connection, row, now)
        if row is not None and row["status"] == "in_run":
            connection.execute("UPDATE death_mission_practice SET token_hash=? WHERE id=?", (token_hash, row["id"]))
        else:
            seed = secrets.token_hex(32)
            state = engine.initial(seed, "balanced", specialists=tuple(engine.SPECIALISTS))
            state["route"][0] = ["archive", "patrol"]
            connection.execute(
                "INSERT INTO death_mission_practice(id,user_id,chat_id,token_hash,status,state_json,seed,expires_at) "
                "VALUES(?,?,?,?,'in_run',?,?,?)",
                (
                    secrets.token_hex(12),
                    user_id,
                    chat_id,
                    token_hash,
                    dump(state),
                    seed,
                    iso(now + timedelta(minutes=15)),
                ),
            )
        return self.view(self.row(connection, token_hash))

    def get(self, connection, token_hash, now):
        return self.view(self.refresh(connection, self.row(connection, token_hash), now))

    def mutate(self, connection, *, token_hash, action, revision, operation_id, choice, now):
        row = self.refresh(connection, self.row(connection, token_hash), now)
        if row is None:
            return self.view(None)
        if not self.missions.available(connection, row):
            return self.view(row, "DISABLED")
        request_hash = hashlib.sha256(dump([action, revision, choice]).encode()).hexdigest()
        previous = connection.execute(
            "SELECT * FROM death_mission_practice_actions WHERE run_id=? AND operation_id=?", (row["id"], operation_id)
        ).fetchone()
        if previous:
            return (
                DeathMissionRun(json.loads(previous["response_json"]))
                if previous["request_hash"] == request_hash
                else self.view(row, "IDEMPOTENCY_CONFLICT")
            )
        error = None
        state = json.loads(row["state_json"])
        if row["status"] != "in_run":
            error = "ALREADY_FINISHED"
        elif revision != row["revision"]:
            error = "STALE_REVISION"
        elif action == "action":
            try:
                state, _ = engine.advance(state, choice.get("id"), row["seed"])
            except ValueError:
                error = "INVALID_ACTION"
        elif action in {"extract", "abandon"}:
            if action == "extract" and not state["checkpoint"]:
                error = "EXTRACTION_LOCKED"
            else:
                state.update(phase="done", outcome="extracted" if action == "extract" else "lost")
        else:
            error = "INVALID_ACTION"
        if not error:
            connection.execute(
                "UPDATE death_mission_practice SET state_json=?,status=?,revision=revision+1 WHERE id=?",
                (dump(state), state["outcome"] or "in_run", row["id"]),
            )
        result = self.view(self.row(connection, token_hash), error)
        connection.execute(
            "INSERT INTO death_mission_practice_actions VALUES(?,?,?,?)",
            (row["id"], operation_id, request_hash, dump(result.payload)),
        )
        return result
