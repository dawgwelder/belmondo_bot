"""Spy Clicker Telegram callbacks."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from .callback_contacts import handle_exchange, handle_npc
from .callback_economy import (
    handle_agency,
    handle_agency_found,
    handle_contact,
    handle_equip,
    handle_prestige,
    handle_unequip,
)
from .callback_investigation import handle_intercept, handle_mole, handle_search
from .callback_menu import handle_menu
from .callback_operations import handle_chase, handle_cooperate, handle_death
from .callback_recruitment import handle_claim
from .context import _service
from .missions import _death_mission_callback
from .achievements import handle_archive


async def spy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if query is None or user is None or chat is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "spy":
        await query.answer("Некорректный сигнал.", show_alert=True)
        return
    _service(context)
    category, value = parts[1], parts[2]
    handler = EXACT_ACTIONS.get(category)
    if handler is None:
        handler = next(
            (
                candidate
                for prefix, candidate in PREFIX_ACTIONS
                if category.startswith(prefix)
            ),
            None,
        )
    if handler is None:
        await query.answer("Неизвестное действие.", show_alert=True)
        return
    await handler(update, context, category, value)


EXACT_ACTIONS = {
    "ach": handle_archive,
    "title": handle_archive,
    "deathmenu": _death_mission_callback,
    "mission": _death_mission_callback,
    "menu": handle_menu,
    "agency": handle_agency,
    "agency_found": handle_agency_found,
    "equip": handle_equip,
    "unequip": handle_unequip,
    "search": handle_search,
    "cooperate": handle_cooperate,
    "chase": handle_chase,
    "death": handle_death,
    "prestige": handle_prestige,
    "claim": handle_claim,
}

PREFIX_ACTIONS = (
    ("contact_", handle_contact),
    ("mole_", handle_mole),
    ("intercept_", handle_intercept),
    ("npc_", handle_npc),
    ("exchange_", handle_exchange),
)
