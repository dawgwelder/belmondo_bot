"""Equipment wear and reservation, within the caller's economic transaction."""
from __future__ import annotations

import sqlite3
from ..equipment import EQUIPMENT_EFFECTS
from ..models import DropReward
from .base import RepositoryComponent


class EquipmentRepository(RepositoryComponent):
    @staticmethod
    def reserve(connection: sqlite3.Connection, user_id: int, item_type: str) -> None:
        maximum = EQUIPMENT_EFFECTS[item_type].charges
        connection.execute(
            """INSERT OR IGNORE INTO item_durability(user_id, item_type, remaining, maximum)
               VALUES (?, ?, ?, ?)""",
            (user_id, item_type, maximum, maximum),
        )

    @staticmethod
    def available(connection: sqlite3.Connection, user_id: int, item_type: str) -> int:
        row = connection.execute(
            """SELECT i.amount - CASE WHEN d.remaining < d.maximum OR e.slot IS NOT NULL
                       THEN 1 ELSE 0 END AS available
               FROM user_items i
               LEFT JOIN item_durability d ON d.user_id=i.user_id AND d.item_type=i.item_type
               LEFT JOIN equipped_items e ON e.user_id=i.user_id AND e.item_type=i.item_type
               WHERE i.user_id=? AND i.item_type=?""",
            (user_id, item_type),
        ).fetchone()
        return max(0, row["available"]) if row else 0

    @staticmethod
    def consume(
        connection: sqlite3.Connection,
        user_id: int,
        item_type: str,
        operation_key: str,
        now_value: str,
    ) -> bool:
        row = connection.execute(
            """SELECT d.remaining FROM item_durability d
               JOIN equipped_items e ON e.user_id=d.user_id AND e.item_type=d.item_type
               JOIN user_items i ON i.user_id=d.user_id AND i.item_type=d.item_type
               WHERE d.user_id=? AND d.item_type=? AND i.amount > 0""",
            (user_id, item_type),
        ).fetchone()
        if row is None:
            return False
        remaining = row["remaining"] - 1
        inserted = connection.execute(
            """INSERT OR IGNORE INTO equipment_uses(operation_key,user_id,item_type,remaining,created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (operation_key, user_id, item_type, remaining, now_value),
        )
        if inserted.rowcount != 1:
            return False
        if remaining:
            connection.execute(
                "UPDATE item_durability SET remaining=? WHERE user_id=? AND item_type=?",
                (remaining, user_id, item_type),
            )
        else:
            connection.execute(
                "UPDATE user_items SET amount=amount-1 WHERE user_id=? AND item_type=?",
                (user_id, item_type),
            )
            connection.execute(
                "DELETE FROM equipped_items WHERE user_id=? AND item_type=?",
                (user_id, item_type),
            )
            connection.execute(
                "DELETE FROM item_durability WHERE user_id=? AND item_type=?",
                (user_id, item_type),
            )
        return True

    def modify_drop(
        self, connection, user_id, event_type, reward, operation_key, now_value
    ):
        item_type = {"dead_drop": "satellite_image", "intercept": "access_code"}.get(
            event_type
        )
        if item_type is None or not self.consume(
            connection, user_id, item_type, operation_key, now_value
        ):
            return reward
        if reward.reward_type == "empty":
            return DropReward("item", "intel_file", 1)
        return DropReward(reward.reward_type, reward.reward_id, reward.amount + 1)
