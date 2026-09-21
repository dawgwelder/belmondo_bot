"""Atomic slot settlement and durable replay; connections belong to the caller."""
import json
import math

from ..slots import COOLDOWN_SECONDS, REEL, RULES_VERSION, multiplier
from .base import RepositoryComponent, _datetime, _iso


class SlotsRepository(RepositoryComponent):
    def __init__(self, context, *, economy):
        super().__init__(context)
        self.economy = economy

    @staticmethod
    def present(row):
        return {
            "operation_id": row["operation_id"],
            "stake": row["stake"],
            "symbols": json.loads(row["symbols_json"]),
            "multiplier": row["multiplier"],
            "payout": row["payout"],
            "net": row["payout"] - row["stake"],
            "balance_after": row["balance_after"],
            "rules_version": row["rules_version"],
            "created_at": row["created_at"],
        }

    def history(self, connection, user_id):
        return [
            self.present(row)
            for row in connection.execute(
                "SELECT * FROM slot_spins WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (user_id,),
            )
        ]

    def spin(
        self,
        connection,
        *,
        user_id,
        chat_id,
        username,
        display_name,
        operation_id,
        stake,
        now
    ):
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if not chat or not chat["enabled"]:
            return {"ok": False, "status": "disabled"}
        previous = connection.execute(
            "SELECT * FROM slot_spins WHERE user_id=? AND operation_id=?",
            (user_id, operation_id),
        ).fetchone()
        if previous:
            if previous["stake"] != stake or previous["chat_id"] != chat_id:
                return {"ok": False, "status": "conflict"}
            return {"ok": True, "status": "success", "spin": self.present(previous)}
        latest = connection.execute(
            "SELECT created_at FROM slot_spins WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if latest:
            remaining = (
                COOLDOWN_SECONDS
                - (now - _datetime(latest["created_at"])).total_seconds()
            )
            if remaining > 0:
                return {
                    "ok": False,
                    "status": "cooldown",
                    "retry_after": math.ceil(remaining),
                }
        self.economy.ensure_user(connection, user_id, username, display_name, _iso(now))
        spent = connection.execute(
            """UPDATE user_agents SET amount=amount-?
               WHERE user_id=? AND agent_type='informant' AND amount>=?""",
            (stake, user_id, stake),
        )
        if spent.rowcount != 1:
            return {"ok": False, "status": "insufficient_agents"}
        symbols = tuple(REEL[self.rng.randint(0, len(REEL) - 1)] for _ in range(3))
        factor = multiplier(symbols)
        payout = stake * factor
        connection.execute(
            "UPDATE user_agents SET amount=amount+? WHERE user_id=? AND agent_type='informant'",
            (payout, user_id),
        )
        balance = connection.execute(
            "SELECT amount FROM user_agents WHERE user_id=? AND agent_type='informant'",
            (user_id,),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO slot_spins(user_id,chat_id,operation_id,stake,symbols_json,
                 multiplier,payout,balance_after,rules_version,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id,
                chat_id,
                operation_id,
                stake,
                json.dumps(symbols),
                factor,
                payout,
                balance,
                RULES_VERSION,
                _iso(now),
            ),
        )
        row = connection.execute(
            "SELECT * FROM slot_spins WHERE user_id=? AND operation_id=?",
            (user_id, operation_id),
        ).fetchone()
        return {"ok": True, "status": "success", "spin": self.present(row)}
