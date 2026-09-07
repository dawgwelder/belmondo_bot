"""Spy Clicker Telegram menu views."""
from __future__ import annotations

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from spy_game.models import Inventory, Profile
from spy_game.settings import AGENT_TYPES, ITEM_TYPES
from .formatting import (
    _countdown,
    _format_costs,
    _format_item_costs,
    _format_npc_recipe_reward,
)


def _menu_keyboard(
    profile: Profile,
    webapp_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    if webapp_url:
        rows.append(
            [InlineKeyboardButton("🗄 Открыть оперативный центр", url=webapp_url)]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🪪 Досье", callback_data="spy:menu:profile"),
                InlineKeyboardButton("🕵️ Агенты", callback_data="spy:menu:agents"),
            ],
            [
                InlineKeyboardButton("🎒 Инвентарь", callback_data="spy:menu:inventory"),
                InlineKeyboardButton("🏆 Рейтинг", callback_data="spy:menu:leaderboard"),
            ],
            [
                InlineKeyboardButton(
                    "⏱ Состояние сети", callback_data="spy:menu:status"
                ),
                InlineKeyboardButton("🔄 Обновить", callback_data="spy:menu:refresh"),
            ],
            [
                InlineKeyboardButton(
                    "⭐ Повысить репутацию",
                    callback_data=f"spy:prestige:{profile.reputation}",
                ),
                InlineKeyboardButton(
                    "🏛 Своя служба",
                    callback_data="spy:agency:status",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎖 Архив особых заслуг",
                    callback_data="spy:menu:achievements",
                )
            ],
            [
                InlineKeyboardButton(
                    "🤝 Контакты Центра",
                    callback_data="spy:menu:contacts",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_contact_blocks(recipes) -> list[dict]:
    names = {
        "handler": "🗂 Куратор",
        "recruiter": "🧑‍💼 Рекрутер",
        "operations_chief": "🎖 Начальник операций",
        "counterintelligence": "🔎 Контрразведка",
    }
    blocks: list[dict] = [{"type": "paragraph", "text": "🤝 ПОСТОЯННЫЕ КОНТАКТЫ ЦЕНТРА"}]
    for npc_id, title in names.items():
        lines = []
        for recipe in recipes:
            if recipe.npc_id != npc_id:
                continue
            costs = [
                part
                for part in (
                    _format_costs(recipe.agent_costs),
                    _format_item_costs(recipe.item_costs),
                )
                if part
            ]
            lines.append(
                f"{recipe.display_name}: {'; '.join(costs)} → "
                f"{_format_npc_recipe_reward(recipe)}"
            )
        if lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": title,
                    "blocks": [{"type": "paragraph", "text": "\n".join(lines)}],
                }
            )
    blocks.append(
        {
            "type": "footer",
            "text": (
                "Эти сделки доступны постоянно. Редкая встреча с NPC даёт "
                "увеличенный результат."
            ),
        }
    )
    return blocks


def _contact_keyboard(recipes) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    recipe.display_name,
                    callback_data=f"spy:contact_{recipe.id}:0",
                )
            ]
            for recipe in recipes
        ]
    )


def build_menu_blocks(profile: Profile, status, now: datetime) -> list[dict]:
    if status.active_event_id:
        event_line = "В чате уже идёт операция — ищите сообщение с кнопкой."
    elif status.enabled:
        event_line = "Сигнал может прийти на пике, по инерции или случайно."
    else:
        event_line = "В этом чате сеть пока не активирована."
    return [
        {"type": "paragraph", "text": "🕵️ SPY CLICKER · ОПЕРАТИВНЫЙ ЦЕНТР"},
        {
            "type": "details",
            "summary": "Ваше досье",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Агентов: {profile.total_agents}\n"
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}"
                    ),
                }
            ],
        },
        {
            "type": "paragraph",
            "text": (f"Активность чата: {status.activity_score:.1f}\n{event_line}"),
        },
        {
            "type": "footer",
            "text": "Пик срабатывает сразу; инерция живёт недолго после беседы.",
        },
    ]


def build_profile_blocks(profile: Profile, achievements=None) -> list[dict]:
    name = (
        f"@{profile.username.lstrip('@')}"
        if profile.username and profile.username.strip("@")
        else "СКРЫТЫЙ АГЕНТ"
    )
    if achievements and achievements["title"]:
        name += f" · {achievements['title']}"
    return [
        {"type": "paragraph", "text": f"🪪 ДОСЬЕ · {name}"},
        {
            "type": "details",
            "summary": "Показатели",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}\n"
                        f"Агентов в сети: {profile.total_agents}"
                    ),
                }
            ],
        },
        {
            "type": "footer",
            "text": (
                f"Заслуги: {achievements['unlocked']}/{achievements['total']} · "
                f"Очки: {achievements['points']} · Новые: {achievements['new_count']}"
                if achievements
                else "Никаких лишних имён. Только результаты."
            ),
        },
    ]


def build_agents_blocks(holdings) -> list[dict]:
    by_tier: dict[int, list[str]] = {}
    for holding in holdings:
        agent = AGENT_TYPES.get(holding.agent_type)
        if agent is None:
            continue
        by_tier.setdefault(agent.tier, []).append(
            f"{agent.emoji} {agent.display_name}: {holding.amount}"
        )
    blocks: list[dict] = [{"type": "paragraph", "text": "🕵️ АГЕНТУРНАЯ СЕТЬ"}]
    if not by_tier:
        blocks.append(
            {
                "type": "paragraph",
                "text": "Сеть пока пуста. Первым замечайте события в чате.",
            }
        )
    else:
        for tier in sorted(by_tier):
            blocks.append(
                {
                    "type": "details",
                    "summary": f"Уровень {tier}",
                    "blocks": [{"type": "paragraph", "text": "\n".join(by_tier[tier])}],
                }
            )
    blocks.append(
        {"type": "footer", "text": "Обычные агенты хранятся общим количеством."}
    )
    return blocks


def build_inventory_blocks(inventory: Inventory) -> list[dict]:
    equipped_by_type = {item.item_type: item.slot for item in inventory.equipped}
    blocks: list[dict] = [{"type": "paragraph", "text": "🎒 ИНВЕНТАРЬ"}]
    if not inventory.items:
        blocks.append(
            {
                "type": "paragraph",
                "text": "Инвентарь пуст. Ищите тайники разведсети.",
            }
        )
    else:
        equipment_lines = []
        consumable_lines = []
        for holding in inventory.items:
            item = ITEM_TYPES.get(holding.item_type)
            if item is None:
                continue
            suffix = (
                f" · слот {equipped_by_type[item.id]}"
                if item.id in equipped_by_type
                else ""
            )
            line = f"{item.emoji} {item.display_name}: {holding.amount}{suffix}"
            target = (
                equipment_lines
                if item.category.value == "equipment"
                else consumable_lines
            )
            target.append(line)
        if equipment_lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": "Экипировка",
                    "blocks": [
                        {"type": "paragraph", "text": "\n".join(equipment_lines)}
                    ],
                }
            )
        if consumable_lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": "Расходные материалы",
                    "blocks": [
                        {"type": "paragraph", "text": "\n".join(consumable_lines)}
                    ],
                }
            )
    blocks.append(
        {
            "type": "footer",
            "text": (
                f"Занято слотов: {len(inventory.equipped)}/{inventory.slot_count}. "
                "Прослушка может усилить награду Recruitment."
            ),
        }
    )
    return blocks


def _inventory_keyboard(inventory: Inventory) -> InlineKeyboardMarkup | None:
    equipped_types = {item.item_type for item in inventory.equipped}
    rows = []
    for holding in inventory.items:
        item = ITEM_TYPES.get(holding.item_type)
        if (
            item is not None
            and item.category.value == "equipment"
            and item.id not in equipped_types
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Надеть: {item.display_name}",
                        callback_data=f"spy:equip:{item.id}",
                    )
                ]
            )
    for equipped in inventory.equipped:
        item = ITEM_TYPES[equipped.item_type]
        rows.append(
            [
                InlineKeyboardButton(
                    f"Снять: {item.display_name}",
                    callback_data=f"spy:unequip:{equipped.slot}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def build_status_blocks(status, now: datetime) -> list[dict]:
    if status.active_event_id:
        state = f"Активная операция: {status.active_event_id}"
        timer = f"Окно закроется: {_countdown(status.active_event_expires_at, now)}"
    elif status.enabled:
        state = "Сеть активна"
        timer = "Триггеры: пик · инерция · случайный сигнал"
    else:
        state = "Сеть не активирована в этом чате"
        timer = "Таймер остановлен"
    blocks = [
        {"type": "paragraph", "text": "⏱ СОСТОЯНИЕ СЕТИ"},
        {
            "type": "paragraph",
            "text": f"{state}\nАктивность: {status.activity_score:.1f}\n{timer}",
        },
    ]
    if status.story_arc:
        blocks.append(
            {
                "type": "details",
                "summary": f"Сюжет: {status.story_arc} · этап {status.story_stage}",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": status.story_summary or "Сводка ещё не сформирована.",
                    }
                ],
            }
        )
    blocks.append(
        {
            "type": "footer",
            "text": "Порог запуска — 6 очков; со временем активность затухает.",
        }
    )
    return blocks


def build_leaderboard_blocks(entries) -> list[dict]:
    lines = [
        (
            f"{entry.rank}. {entry.display_name} — сеть {entry.total_agents}, "
            f"Tier 3: {entry.rare_agents}, репутация {entry.reputation}, "
            f"служба {entry.agency_level}"
        )
        for entry in entries
    ]
    return [
        {"type": "paragraph", "text": "🏆 РЕЙТИНГ РАЗВЕДСЕТЕЙ"},
        {
            "type": "paragraph",
            "text": "\n".join(lines) if lines else "Пока ни одно досье не открыто.",
        },
        {
            "type": "footer",
            "text": "Порядок: уровень службы, репутация, Tier 3, общий состав.",
        },
    ]
