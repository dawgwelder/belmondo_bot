"""Economy persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from ..models import (
    AgentCost,
    AgentHolding,
    DropReward,
    EquipmentResult,
    EquipmentStatus,
    EquippedItem,
    Inventory,
    ItemCost,
    ItemHolding,
    LeaderboardEntry,
    Profile,
    Reward,
)
from ..settings import AGENT_TYPES, ITEM_TYPES

from .base import RepositoryComponent, _iso


class EconomyRepository(RepositoryComponent):
    def ensure_user_and_profile(
        self,
        connection: sqlite3.Connection,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> Profile:
        now_value = _iso(now)
        self.ensure_user(connection, user_id, username, display_name, now_value)
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
                display_name=self.user_label(row["username"], None),
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
    def item_is_equipped(
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
    def ensure_user(
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
    def user_label(username: str | None, _display_name: str | None) -> str:
        if username and username.strip("@"):
            return f"@{username.lstrip('@')}"
        return "Скрытый агент"

    @staticmethod
    def has_costs(
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
    def spend_costs(
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
    def has_item_costs(
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
    def spend_item_costs(
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
    def add_reward(
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
    def add_drop_reward(
        cls,
        connection: sqlite3.Connection,
        user_id: int,
        reward: DropReward,
    ) -> None:
        if reward.reward_type == "agent":
            cls.add_reward(
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
