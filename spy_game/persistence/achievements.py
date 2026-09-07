"""Incremental achievement projection inside the caller's transaction.

SQLite triggers capture inventory snapshots and journal references. Processing
each queued fact once avoids rescanning lifetime history on every click.
"""
from __future__ import annotations

import json

from ..achievements import catalogue
from ..death_mission import TACTICS
from ..settings import AGENT_TYPES


def _object(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _bundle(value):
    if isinstance(value, list):
        value = {
            r.get("agent_type", r.get("id")): r.get("amount", 0)
            for r in value
            if isinstance(r, dict)
        }
    if not isinstance(value, dict):
        return {}
    return {
        k: v for k, v in value.items() if k in AGENT_TYPES and type(v) is int and v > 0
    }


class AchievementsRepository:
    def __init__(self, settings):
        self.definitions = catalogue(settings)

    @staticmethod
    def peak(connection, user_id, metric, value):
        if type(value) is not int or value < 0:
            return
        connection.execute(
            "INSERT INTO achievement_metrics VALUES(?,?,?) "
            "ON CONFLICT(user_id,metric) DO UPDATE SET value=max(value,excluded.value)",
            (user_id, metric, value),
        )

    @staticmethod
    def increment(connection, user_id, metric):
        connection.execute(
            "INSERT INTO achievement_metrics VALUES(?,?,1) "
            "ON CONFLICT(user_id,metric) DO UPDATE SET value=value+1",
            (user_id, metric),
        )

    def member(self, connection, user_id, metric, member):
        connection.execute(
            "INSERT OR IGNORE INTO achievement_members VALUES(?,?,?)",
            (user_id, metric, str(member)),
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM achievement_members WHERE user_id=? AND metric=?",
            (user_id, metric),
        ).fetchone()[0]
        self.peak(connection, user_id, metric, count)

    def agents_seen(self, connection, user_id, bundle):
        agents = _bundle(bundle)
        if agents:
            self.peak(connection, user_id, "network", 1)
        for agent in agents:
            self.member(connection, user_id, "collection", agent)
            if agent == "ghost_agent":
                self.peak(connection, user_id, "ghost", 1)
            if agent == "intelligence_director":
                self.peak(connection, user_id, "director", 1)

    def inventory(self, connection, user_id, payload, *, historical=False):
        bundle = _bundle(payload)
        total = sum(bundle.values())
        self.agents_seen(connection, user_id, bundle)
        self.peak(connection, user_id, "network", total)
        self.peak(connection, user_id, "fullstaff", len(bundle))
        self.peak(connection, user_id, "double", bundle.get("double_agent", 0))
        lost = connection.execute(
            "SELECT value FROM achievement_metrics WHERE user_id=? AND metric='lost_empty'",
            (user_id,),
        ).fetchone()
        if lost and not historical:
            self.peak(connection, user_id, "comeback", total)

    def drain(self, connection):
        # A bounded batch also keeps first-start backfill memory bounded.
        while rows := connection.execute(
            "SELECT * FROM achievement_queue ORDER BY id LIMIT 500"
        ).fetchall():
            affected = {}
            for row in rows:
                uid = row["user_id"]
                payload = _object(row["payload_json"])
                kind = row["kind"]
                if kind == "inventory":
                    self.inventory(
                        connection, uid, payload, historical=bool(row["historical"])
                    )
                elif kind == "progress":
                    for metric in ("reputation", "agency", "office"):
                        self.peak(connection, uid, metric, payload.get(metric, 0))
                else:
                    self.journal(
                        connection, uid, kind, payload, bool(row["historical"])
                    )
                affected[uid] = row["created_at"]
            for uid, timestamp in affected.items():
                self.unlock(connection, uid, timestamp)
            connection.execute(
                "DELETE FROM achievement_queue WHERE id <= ?", (rows[-1]["id"],)
            )

    def journal(self, connection, uid, kind, payload, historical):
        tables = {
            "event": "event_history",
            "economy": "economy_history",
            "agency": "agency_history",
            "duel": "spy_duel_history",
        }
        table = tables.get(kind)
        if table is None:
            return
        row = connection.execute(
            f"SELECT * FROM {table} WHERE id=?", (payload.get("id"),)
        ).fetchone()
        if row is None:
            return
        meta = _object(row["metadata_json"])
        for field in ("costs", "agent_costs", "rewards"):
            self.agents_seen(connection, uid, meta.get(field))
        if kind == "agency":
            self.peak(connection, uid, "agency", row["to_level"])
            self.peak(connection, uid, "reputation", meta.get("reputation_spent", 0))
        elif kind == "economy":
            if row["action"] == "prestige":
                self.peak(connection, uid, "reputation", meta.get("to", 0))
            elif meta.get("source") == "operational_center":
                npc = meta.get("npc_id")
                if npc in {
                    "handler",
                    "recruiter",
                    "operations_chief",
                    "counterintelligence",
                }:
                    self.member(connection, uid, "contacts", npc)
                if meta.get("reward_type") == "agent":
                    self.agents_seen(
                        connection,
                        uid,
                        {meta.get("reward_id"): meta.get("reward_amount")},
                    )
        elif kind == "duel":
            if row["outcome"] == "moves" and row["winner_user_id"] == uid:
                opponent = (
                    row["opponent_user_id"]
                    if uid == row["challenger_user_id"]
                    else row["challenger_user_id"]
                )
                if opponent is not None and opponent != uid:
                    self.member(connection, uid, "opponents", opponent)
        elif kind == "event":
            self.event(connection, uid, row, meta, payload, historical)

    def event(self, connection, uid, row, meta, payload, historical):
        event, outcome = row["event_type"], row["outcome"]
        if row["reward_type"] == "agent":
            self.agents_seen(connection, uid, {row["reward_id"]: row["reward_amount"]})
        agent_reward = meta.get("agent_reward")
        if isinstance(agent_reward, dict):
            self.agents_seen(connection, uid, [agent_reward])
        if event == "intercept" and outcome in {"correct", "won"}:
            self.increment(connection, uid, "intercept")
        elif event == "dead_drop" and outcome in {"searched", "won"}:
            if outcome == "won":
                self.increment(connection, uid, "codes")
            if row["reward_type"] == "empty":
                self.peak(connection, uid, "empty", 1)
        elif event == "find_mole" and outcome == "won":
            self.increment(connection, uid, "moles")
        elif event == "cooperative_operation" and outcome == "rewarded":
            self.increment(connection, uid, "coop")
        elif event == "chase" and outcome == "rewarded":
            role = meta.get("role")
            if role in {"starter", "interceptor"}:
                self.member(connection, uid, "chase_roles", role)
                # Read the two rewards of this event, without a lifetime per-event set.
                roles = {
                    _object(r[0]).get("role")
                    for r in connection.execute(
                        "SELECT metadata_json FROM event_history WHERE event_id=? "
                        "AND user_id=? AND event_type='chase' AND outcome='rewarded'",
                        (row["event_id"], uid),
                    )
                }
                if {"starter", "interceptor"} <= roles:
                    self.peak(connection, uid, "solo", 1)
        elif event in {"npc", "handler"} and outcome == "exchanged":
            if meta.get("reward_multiplier", 1) >= 2:
                self.peak(connection, uid, "raredeal", 1)
        elif event == "death_operation":
            stake = _bundle(meta.get("stake", meta.get("staked")))
            if stake:
                self.inventory(connection, uid, stake, historical=True)
            if outcome == "won":
                self.increment(connection, uid, "death_wins")
            if outcome == "lost" and not historical and payload.get("balance") == 0:
                self.peak(connection, uid, "lost_empty", 1)
            result = meta.get("result", {})
            if isinstance(result, dict):
                for field in ("returned", "bonus"):
                    self.agents_seen(connection, uid, result.get(field))
            run = connection.execute(
                "SELECT mode,tactic,state_json,result_json FROM death_mission_runs "
                "WHERE event_id=? AND user_id=? AND committed_at IS NOT NULL",
                (row["event_id"], uid),
            ).fetchone()
            if run is None or run["mode"] != "mission":
                return
            state = _object(run["state_json"])
            if outcome == "won":
                self.increment(connection, uid, "personal_wins")
                if run["tactic"] in TACTICS:
                    self.member(connection, uid, "tactics", run["tactic"])
                if state.get("hp") == 1:
                    self.peak(connection, uid, "lastbreath", 1)
                if state.get("survived_raid"):
                    self.peak(connection, uid, "raid", 1)
            elif outcome == "extracted" and _bundle(
                _object(run["result_json"]).get("returned")
            ):
                self.peak(connection, uid, "extract", 1)

    def unlock(self, connection, uid, timestamp):
        metrics = dict(
            connection.execute(
                "SELECT metric,value FROM achievement_metrics WHERE user_id=?", (uid,)
            ).fetchall()
        )
        for definition in self.definitions:
            if metrics.get(definition.metric, 0) >= definition.target:
                connection.execute(
                    "INSERT OR IGNORE INTO user_achievements(user_id,achievement_id,unlocked_at) VALUES(?,?,?)",
                    (uid, definition.id, timestamp),
                )

    def archive(self, connection, user_id):
        metrics = dict(
            connection.execute(
                "SELECT metric,value FROM achievement_metrics WHERE user_id=?",
                (user_id,),
            ).fetchall()
        )
        unlocked = {
            r["achievement_id"]: r
            for r in connection.execute(
                "SELECT * FROM user_achievements WHERE user_id=?", (user_id,)
            )
        }
        selected = connection.execute(
            "SELECT achievement_id FROM achievement_titles WHERE user_id=?", (user_id,)
        ).fetchone()
        title_id = selected[0] if selected else None
        entries = []
        for a in self.definitions:
            owned = unlocked.get(a.id)
            hidden = a.secret and owned is None
            entries.append(
                dict(
                    id=a.id,
                    name="Засекречено" if hidden else a.name,
                    description="Центр пока не раскрывает условия этой заслуги."
                    if hidden
                    else a.description,
                    progress=None
                    if hidden
                    else min(a.target, metrics.get(a.metric, 0)),
                    target=None if hidden else a.target,
                    points=a.points,
                    secret=a.secret,
                    unlocked=owned is not None,
                    unlocked_at=owned["unlocked_at"] if owned else None,
                    is_new=bool(owned and not owned["seen"]),
                    note=a.note if owned else None,
                )
            )
        return dict(
            entries=entries,
            unlocked=sum(e["unlocked"] for e in entries),
            total=len(entries),
            points=sum(e["points"] for e in entries if e["unlocked"]),
            new_count=sum(e["is_new"] for e in entries),
            title_id=title_id,
            title=next((a.name for a in self.definitions if a.id == title_id), None),
        )

    def select_title(self, connection, user_id, achievement_id):
        if achievement_id is None:
            connection.execute(
                "DELETE FROM achievement_titles WHERE user_id=?", (user_id,)
            )
            return True
        if achievement_id not in {a.id for a in self.definitions}:
            return False
        owned = connection.execute(
            "SELECT 1 FROM user_achievements WHERE user_id=? AND achievement_id=?",
            (user_id, achievement_id),
        ).fetchone()
        if not owned:
            return False
        connection.execute(
            "INSERT INTO achievement_titles VALUES(?,?) ON CONFLICT(user_id) "
            "DO UPDATE SET achievement_id=excluded.achievement_id",
            (user_id, achievement_id),
        )
        return True

    @staticmethod
    def mark_seen(connection, user_id, achievement_ids):
        connection.executemany(
            "UPDATE user_achievements SET seen=1 WHERE user_id=? AND achievement_id=?",
            [(user_id, key) for key in achievement_ids],
        )
