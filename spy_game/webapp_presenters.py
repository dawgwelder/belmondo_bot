"""JSON presentation for Mini App state and HTML5 game results."""
from __future__ import annotations

from .models import DeadDropGameRun, FindMoleGameRun, InterceptGameRun
from .service import SpyGameService
from .settings import AGENT_TYPES, ITEM_TYPES
from .death_mission_ui import text as mission_text
from .webapp_support import RequestIdentity


async def state_payload(service: SpyGameService, identity: RequestIdentity) -> dict:
    user = identity.user
    profile = await service.get_profile(
        user_id=user.user_id,
        username=user.username,
        display_name=user.display_name,
    )
    agents = await service.get_agents(user.user_id)
    inventory = await service.get_inventory(user.user_id)
    achievements = await service.get_achievements(user.user_id)
    leaderboard = await service.get_leaderboard()
    chat_status = (
        await service.get_chat_status(identity.chat_id)
        if identity.chat_id is not None
        else None
    )
    prestige_costs = service.settings.prestige_costs(profile.reputation)
    agency_at_cap = profile.agency_level >= service.settings.agency_max_level
    agency_costs = (
        ()
        if agency_at_cap
        else service.settings.agency_requirements(profile.agency_level)
    )
    contact_names = {
        "handler": "Куратор",
        "recruiter": "Рекрутер",
        "operations_chief": "Начальник операций",
        "counterintelligence": "Контрразведка",
    }
    return {
        "achievements": achievements,
        "profile": {
            "username": f"@{profile.username.lstrip('@')}"
            if profile.username
            else None,
            "reputation": profile.reputation,
            "agency_level": profile.agency_level,
            "agency_max_level": service.settings.agency_max_level,
            "total_agents": profile.total_agents,
        },
        "reserved_agents": await service.reserved_mission_agents(user.user_id),
        "agents": [
            {
                "id": holding.agent_type,
                "name": AGENT_TYPES[holding.agent_type].display_name,
                "emoji": AGENT_TYPES[holding.agent_type].emoji,
                "tier": AGENT_TYPES[holding.agent_type].tier,
                "amount": holding.amount,
            }
            for holding in agents
            if holding.agent_type in AGENT_TYPES
        ],
        "inventory": {
            "slot_count": inventory.slot_count,
            "items": [
                {
                    "id": holding.item_type,
                    "name": ITEM_TYPES[holding.item_type].display_name,
                    "emoji": ITEM_TYPES[holding.item_type].emoji,
                    "category": ITEM_TYPES[holding.item_type].category.value,
                    "amount": holding.amount,
                }
                for holding in inventory.items
                if holding.item_type in ITEM_TYPES
            ],
            "equipped": [
                {
                    "slot": item.slot,
                    "item_type": item.item_type,
                    "name": ITEM_TYPES[item.item_type].display_name,
                    "emoji": ITEM_TYPES[item.item_type].emoji,
                }
                for item in inventory.equipped
                if item.item_type in ITEM_TYPES
            ],
        },
        "leaderboard": [
            {
                "rank": entry.rank,
                "name": entry.display_name,
                "total_agents": entry.total_agents,
                "rare_agents": entry.rare_agents,
                "reputation": entry.reputation,
                "agency_level": entry.agency_level,
            }
            for entry in leaderboard
        ],
        "prestige": {
            "expected_reputation": profile.reputation,
            "costs": agent_costs(prestige_costs),
        },
        "agency": {
            "at_cap": agency_at_cap,
            "expected_level": profile.agency_level,
            "required_reputation": (
                0
                if agency_at_cap
                else service.settings.agency_reputation_requirement(
                    profile.agency_level
                )
            ),
            "costs": agent_costs(agency_costs),
            "rare_bonus_percent": min(
                profile.agency_level * service.settings.agency_rare_bonus_percent,
                service.settings.agency_max_level
                * service.settings.agency_rare_bonus_percent,
            ),
        },
        "contacts": [
            {
                "id": recipe.id,
                "npc_id": recipe.npc_id,
                "npc_name": contact_names[recipe.npc_id],
                "name": recipe.display_name,
                "agent_costs": agent_costs(recipe.agent_costs),
                "item_costs": item_costs(recipe.item_costs),
                "reward": contact_reward(recipe),
            }
            for recipe in service.settings.permanent_contact_recipes
        ],
        "context": {
            "chat_bound": identity.chat_id is not None,
            "can_mutate": bool(chat_status and chat_status.enabled),
            "network_enabled": bool(chat_status and chat_status.enabled),
            "activity_score": chat_status.activity_score if chat_status else None,
            "activity_profile": chat_status.activity_profile if chat_status else None,
            "active_event": bool(chat_status and chat_status.active_event_id),
        },
    }


def agent_costs(costs) -> list[dict]:
    return [
        {
            "id": cost.agent_type,
            "name": AGENT_TYPES[cost.agent_type].display_name,
            "emoji": AGENT_TYPES[cost.agent_type].emoji,
            "amount": cost.amount,
        }
        for cost in costs
    ]


def item_costs(costs) -> list[dict]:
    return [
        {
            "id": cost.item_type,
            "name": ITEM_TYPES[cost.item_type].display_name,
            "emoji": ITEM_TYPES[cost.item_type].emoji,
            "amount": cost.amount,
        }
        for cost in costs
    ]


def drop_entry(reward) -> dict:
    registry = AGENT_TYPES if reward.reward_type == "agent" else ITEM_TYPES
    definition = registry[reward.reward_id]
    return {
        "type": reward.reward_type,
        "id": reward.reward_id,
        "name": definition.display_name,
        "emoji": definition.emoji,
        "amount": reward.amount,
    }


def contact_reward(recipe) -> dict:
    if len(recipe.rewards) == 1:
        return drop_entry(recipe.rewards[0])
    if all(reward.reward_type == "agent" for reward in recipe.rewards):
        tiers = sorted(
            {
                AGENT_TYPES[reward.reward_id].tier
                for reward in recipe.rewards
                if reward.reward_id in AGENT_TYPES
            }
        )
        tier_label = (
            f"Tier {tiers[0]}–{tiers[-1]}" if len(tiers) > 1 else f"Tier {tiers[0]}"
        )
        name = f"Случайный агент {tier_label}"
    else:
        name = "Случайный результат"
    return {
        "type": "random",
        "id": None,
        "name": name,
        "emoji": "🎲",
        "amount": recipe.rewards[0].amount,
    }


def intercept_game_payload(result: InterceptGameRun) -> dict:
    reward = None
    if result.reward is not None and result.reward.reward_id in ITEM_TYPES:
        item = ITEM_TYPES[result.reward.reward_id]
        reward = {
            "id": item.id,
            "name": item.display_name,
            "emoji": item.emoji,
            "amount": result.reward.amount,
        }
    return {
        "game_type": "intercept",
        "status": result.status.value,
        "prompt": result.prompt,
        "targets": list(result.targets),
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "success_score": result.success_score,
        "score": result.score,
        "reward": reward,
    }


def dead_drop_game_payload(result: DeadDropGameRun) -> dict:
    reward = None
    if result.reward is not None:
        if (
            result.reward.reward_type == "item"
            and result.reward.reward_id in ITEM_TYPES
        ):
            item = ITEM_TYPES[result.reward.reward_id]
            reward = {
                "type": "item",
                "id": item.id,
                "name": item.display_name,
                "emoji": item.emoji,
                "amount": result.reward.amount,
            }
        elif (
            result.reward.reward_type == "agent"
            and result.reward.reward_id in AGENT_TYPES
        ):
            agent = AGENT_TYPES[result.reward.reward_id]
            reward = {
                "type": "agent",
                "id": agent.id,
                "name": agent.display_name,
                "emoji": agent.emoji,
                "amount": result.reward.amount,
            }
        else:
            reward = {
                "type": "empty",
                "id": None,
                "name": "Тайник пуст",
                "emoji": "∅",
                "amount": 0,
            }
    return {
        "game_type": "dead_drop",
        "status": result.status.value,
        "code_length": result.code_length,
        "attempts": [
            {
                "digits": list(attempt.digits),
                "exact": attempt.exact,
                "misplaced": attempt.misplaced,
            }
            for attempt in result.attempts
        ],
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "reward": reward,
    }


def find_mole_game_payload(result: FindMoleGameRun) -> dict:
    rewards = []
    if result.item_reward is not None:
        item = ITEM_TYPES.get(result.item_reward.reward_id or "")
        if item is not None:
            rewards.append(
                {
                    "type": "item",
                    "id": item.id,
                    "name": item.display_name,
                    "emoji": item.emoji,
                    "amount": result.item_reward.amount,
                }
            )
    if result.agent_reward is not None:
        agent = AGENT_TYPES.get(result.agent_reward.agent_type)
        if agent is not None:
            rewards.append(
                {
                    "type": "agent",
                    "id": agent.id,
                    "name": agent.display_name,
                    "emoji": agent.emoji,
                    "amount": result.agent_reward.amount,
                }
            )
    return {
        "game_type": "find_mole",
        "status": result.status.value,
        "title": result.title,
        "briefing": result.briefing,
        "clues": list(result.clues),
        "suspects": [
            {
                "id": suspect.id,
                "codename": suspect.codename,
                "role": suspect.role,
                "dossier": suspect.dossier,
            }
            for suspect in result.suspects
        ],
        "revision": result.revision,
        "expires_at": result.expires_at.isoformat() if result.expires_at else None,
        "rewards": rewards,
    }


def death_payload(result):
    return {**result.payload, "text": mission_text(result.payload)}
