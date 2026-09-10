"""Spy Clicker Telegram jobs."""
from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game import death_mission_ui
from .context import _service
from .publication import publish_spy_event
from .event_views import _recruitment_message_text
from spy_game.telegram_messages import compact_event_message


async def track_spy_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if (
        user is None
        or user.is_bot
        or chat is None
        or chat.type not in {"group", "supergroup"}
        or context.bot_data.get("paused", False)
    ):
        return
    await _service(context).record_activity(chat.id, user.id)


async def spy_game_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    try:
        result = await service.tick()
    except Exception:
        logger.exception("spy_game: scheduler tick failed")
        return
    await death_mission_ui.publish_pending(service, context.bot)
    for expired in result.expired:
        # Personal mission results have their own persistent delivery queue.
        if expired.result_managed:
            continue
        locks = context.bot_data.setdefault("spy_recruitment_edit_locks", {})
        async with locks.setdefault(expired.event_id, asyncio.Lock()):
            title = {
                "recruitment": "Набор",
                "dead_drop": "Тайник",
                "intercept": "Перехват",
                "find_mole": "Расследование",
                "cooperative_operation": "Совместная операция",
                "chase": "Погоня",
                "handler": "Встреча с куратором",
                "npc": "Специальная сделка",
                "death_operation": "Смертельная операция",
            }.get(expired.event_type, "Событие")
            text = (
                f"🚫 {title}: событие отменено."
                if expired.cancelled
                else f"⌛ {title}: время истекло."
            )
            if expired.event_type == "recruitment" and not expired.cancelled:
                progress = await service.get_recruitment_progress(expired.event_id)
                if progress is not None:
                    text = _recruitment_message_text("", progress)
            await compact_event_message(
                context.bot, expired.chat_id, expired.message_id, text
            )
    for event in result.spawned:
        try:
            message_id = await publish_spy_event(context, event)
            await service.attach_message(event.event_id, message_id)
            logger.info(
                "spy_event_created event_id=%s chat_id=%s event_type=%s expires_at=%s",
                event.event_id,
                event.chat_id,
                event.event_type,
                event.expires_at.isoformat(),
            )
        except Exception:
            logger.exception(
                "spy_game: event publication failed event_id=%s", event.event_id
            )
            await service.cancel_publication(event.event_id)
