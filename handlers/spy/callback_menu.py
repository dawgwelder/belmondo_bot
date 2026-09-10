"""Spy Clicker Telegram callback menu."""
from __future__ import annotations

from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes
from spy_game import death_mission_ui
from .context import _service
from .menu import _profile_for_update, _send_menu
from .menu_views import (
    _contact_keyboard,
    _inventory_keyboard,
    build_agents_blocks,
    build_contact_blocks,
    build_inventory_blocks,
    build_leaderboard_blocks,
    build_profile_blocks,
    build_status_blocks,
)
from .transport import _send_temporary_rich
from .achievements import send_archive


async def handle_menu(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    if value not in {
        "refresh",
        "profile",
        "agents",
        "inventory",
        "contacts",
        "leaderboard",
        "status",
        "achievements",
    }:
        await query.answer("Неизвестный пункт меню.", show_alert=True)
        return
    await query.answer()
    if value == "refresh":
        await _send_menu(update, context)
        return
    if value == "achievements":
        await _profile_for_update(update, service)
        await send_archive(update, context)
        return
    reply_markup = None
    if value == "profile":
        profile = await _profile_for_update(update, service)
        achievements = await service.get_achievements(user.id)
        blocks = build_profile_blocks(profile, achievements)
        fallback = (
            f"Досье: репутация {profile.reputation}, "
            f"агентов {profile.total_agents}."
        )
        if achievements["title"]:
            fallback += f"\nТитул: {achievements['title']}"
        fallback += f"\nЗаслуги: {achievements['unlocked']}/{achievements['total']} · Новые: {achievements['new_count']}"
    elif value == "agents":
        await _profile_for_update(update, service)
        holdings = await service.get_agents(user.id)
        blocks = build_agents_blocks(holdings)
        fallback = "Агентурная сеть: " + (
            ", ".join(f"{item.agent_type} ×{item.amount}" for item in holdings)
            or "пока пуста"
        )
        reserved = await service.reserved_mission_agents(user.id)
        if reserved:
            copy = "На Смертельной операции: " + death_mission_ui.bundle_text(reserved)
            blocks.append({"type": "paragraph", "text": copy})
            fallback += "\n" + copy
    elif value == "inventory":
        await _profile_for_update(update, service)
        inventory = await service.get_inventory(user.id)
        blocks = build_inventory_blocks(inventory)
        reply_markup = _inventory_keyboard(inventory)
        fallback = "Инвентарь: " + (
            ", ".join(f"{item.item_type} ×{item.amount}" for item in inventory.items)
            or "пока пуст"
        )
    elif value == "contacts":
        recipes = service.settings.permanent_contact_recipes
        blocks = build_contact_blocks(recipes)
        reply_markup = _contact_keyboard(recipes)
        fallback = "Постоянные контакты Центра:\n" + "\n".join(
            recipe.display_name for recipe in recipes
        )
    elif value == "leaderboard":
        entries = await service.get_leaderboard()
        blocks = build_leaderboard_blocks(entries)
        fallback = "Рейтинг разведсетей:\n" + (
            "\n".join(
                f"{entry.rank}. {entry.display_name}: {entry.total_agents}"
                for entry in entries
            )
            or "пока пуст"
        )
    elif value == "status":
        status = await service.get_chat_status(chat.id)
        now = datetime.now(timezone.utc)
        blocks = build_status_blocks(status, now)
        fallback = (
            f"Активность: {status.activity_score:.1f}. "
            "Триггеры: пик, инерция, случайный сигнал."
        )
    await _send_temporary_rich(
        context,
        chat,
        blocks,
        fallback_text=fallback,
        reply_markup=reply_markup,
    )
    return
