"""Contacts persistence; uses the caller-owned SQLite transaction."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from ..models import (
    ContactExchangeResult,
    DropReward,
    EconomyStatus,
    ExchangeResult,
    NpcResult,
    NpcStatus,
)
from .economy import EconomyRepository
from .lifecycle import LifecycleRepository
from .base import RepositoryComponent, RepositoryContext, _iso


class ContactsRepository(RepositoryComponent):
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
        payload = json.loads(event["payload_json"])
        reward_multiplier = payload.get("reward_multiplier", 1)
        if type(reward_multiplier) is not int or reward_multiplier not in {
            1,
            self.settings.handler_event_reward_multiplier,
        }:
            return ExchangeResult(EconomyStatus.INVALID_RECIPE, event_id, recipe_id)
        if event["status"] == "expired":
            return ExchangeResult(EconomyStatus.EXPIRED, event_id, recipe_id)
        if event["status"] != "active":
            return ExchangeResult(EconomyStatus.ALREADY_RESOLVED, event_id, recipe_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return ExchangeResult(EconomyStatus.EXPIRED, event_id, recipe_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        if not self.economy.has_costs(connection, user_id, recipe.costs):
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

        reward = self.reward_resolver.resolve_exchange(
            recipe,
            self.rng,
            amount_multiplier=reward_multiplier,
        )
        self.economy.spend_costs(connection, user_id, recipe.costs)
        self.economy.add_reward(connection, user_id, reward)
        metadata = json.dumps(
            {
                "recipe_id": recipe.id,
                "costs": {cost.agent_type: cost.amount for cost in recipe.costs},
                "reward_multiplier": reward_multiplier,
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
        self.lifecycle.advance_story(connection, chat_id, "handler", now_value)
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
        reward_multiplier = payload.get("reward_multiplier", 1)
        if type(reward_multiplier) is not int or reward_multiplier not in {
            1,
            self.settings.npc_event_reward_multiplier,
        }:
            return NpcResult(NpcStatus.INVALID_RECIPE, event_id, recipe_id)
        if event["status"] == "expired":
            return NpcResult(NpcStatus.EXPIRED, event_id, recipe_id)
        if event["status"] != "active":
            return NpcResult(NpcStatus.ALREADY_RESOLVED, event_id, recipe_id)
        now_value = _iso(now)
        if event["expires_at"] <= now_value:
            self.lifecycle.expire_row(connection, event, now_value)
            return NpcResult(NpcStatus.EXPIRED, event_id, recipe_id)

        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        if not self.economy.has_costs(connection, user_id, recipe.agent_costs) or not (
            self.economy.has_item_costs(connection, user_id, recipe.item_costs)
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
        self.economy.spend_costs(connection, user_id, recipe.agent_costs)
        self.economy.spend_item_costs(connection, user_id, recipe.item_costs)
        reward = self.reward_resolver.resolve_npc(
            recipe,
            agency_level,
            self.rng,
            amount_multiplier=reward_multiplier,
        )
        self.economy.add_drop_reward(connection, user_id, reward)
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
                "reward_multiplier": reward_multiplier,
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
        self.lifecycle.advance_story(connection, chat_id, "npc", now_value)
        return NpcResult(
            NpcStatus.SUCCESS,
            event_id,
            recipe_id,
            reward=reward,
            required_agents=recipe.agent_costs,
            required_items=recipe.item_costs,
        )

    def exchange_with_contact(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        recipe_id: str,
        chat_id: int,
        user_id: int,
        username: str | None,
        display_name: str | None,
        now: datetime,
    ) -> ContactExchangeResult:
        recipe = self.settings.permanent_contact_recipe(recipe_id)
        if recipe is None:
            return ContactExchangeResult(
                NpcStatus.INVALID_RECIPE,
                operation_id,
                recipe_id,
            )
        chat = connection.execute(
            "SELECT enabled FROM chat_state WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
        if chat is None or not chat["enabled"]:
            return ContactExchangeResult(
                NpcStatus.DISABLED,
                operation_id,
                recipe_id,
            )

        idempotency_key = f"contact:{user_id}:{operation_id}"
        existing = connection.execute(
            """
            SELECT recipe_id, metadata_json
            FROM economy_history
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        ).fetchone()
        if existing is not None:
            if existing["recipe_id"] != recipe_id:
                return ContactExchangeResult(
                    NpcStatus.INVALID_RECIPE,
                    operation_id,
                    recipe_id,
                )
            metadata = json.loads(existing["metadata_json"])
            reward = DropReward(
                metadata["reward_type"],
                metadata["reward_id"],
                metadata["reward_amount"],
            )
            return ContactExchangeResult(
                NpcStatus.SUCCESS,
                operation_id,
                recipe_id,
                reward=reward,
                required_agents=recipe.agent_costs,
                required_items=recipe.item_costs,
            )

        now_value = _iso(now)
        self.economy.ensure_user(connection, user_id, username, display_name, now_value)
        if not self.economy.has_costs(connection, user_id, recipe.agent_costs) or not (
            self.economy.has_item_costs(connection, user_id, recipe.item_costs)
        ):
            return ContactExchangeResult(
                NpcStatus.INSUFFICIENT_RESOURCES,
                operation_id,
                recipe_id,
                required_agents=recipe.agent_costs,
                required_items=recipe.item_costs,
            )

        agency_level = connection.execute(
            "SELECT agency_level FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()[0]
        self.economy.spend_costs(connection, user_id, recipe.agent_costs)
        self.economy.spend_item_costs(connection, user_id, recipe.item_costs)
        reward = self.reward_resolver.resolve_npc(recipe, agency_level, self.rng)
        self.economy.add_drop_reward(connection, user_id, reward)
        metadata = json.dumps(
            {
                "source": "operational_center",
                "chat_id": chat_id,
                "npc_id": recipe.npc_id,
                "recipe_id": recipe.id,
                "agent_costs": {
                    cost.agent_type: cost.amount for cost in recipe.agent_costs
                },
                "item_costs": {
                    cost.item_type: cost.amount for cost in recipe.item_costs
                },
                "agency_level": agency_level,
                "reward_type": reward.reward_type,
                "reward_id": reward.reward_id,
                "reward_amount": reward.amount,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO economy_history(
                idempotency_key, user_id, action, source_event_id,
                recipe_id, metadata_json, created_at
            ) VALUES (?, ?, 'exchange', NULL, ?, ?, ?)
            """,
            (
                idempotency_key,
                user_id,
                recipe.id,
                metadata,
                now_value,
            ),
        )
        return ContactExchangeResult(
            NpcStatus.SUCCESS,
            operation_id,
            recipe_id,
            reward=reward,
            required_agents=recipe.agent_costs,
            required_items=recipe.item_costs,
        )
