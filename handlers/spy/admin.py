"""Spy Clicker Telegram admin."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.models import ChatStatus
from spy_game.scheduler import ActivityTriggerSettings
from .constants import ACTIVITY_PROFILE_ALIASES, ACTIVITY_PROFILE_LABELS
from .context import _service
from .menu_views import build_status_blocks
from .publication import publish_spy_event
from .transport import _remove_event_keyboard, _send_rich


def build_activity_admin_text(
    status: ChatStatus,
    trigger: ActivityTriggerSettings,
) -> str:
    label = ACTIVITY_PROFILE_LABELS[trigger.profile]
    return (
        "⚙️ ЧАСТОТА СОБЫТИЙ\n"
        f"Профиль: {label} ({trigger.profile})\n"
        f"Текущий score: {status.activity_score:.1f}\n"
        f"Peak: score ≥ {trigger.threshold:g} и сообщений за tick ≥ "
        f"{trigger.peak_messages}\n"
        f"Inertia: 1/{trigger.inertia_one_in} за tick в течение "
        f"{math.ceil(trigger.inertia_window_seconds / 60)} мин.\n"
        f"Случайный сигнал: в среднем раз в "
        f"{math.ceil(trigger.random_average_seconds / 60)} мин.\n"
        f"Cooldown: {math.ceil(trigger.event_cooldown_seconds / 60)} мин.\n\n"
        "Переключение: /spy_admin activity "
        "calm|balanced|aggressive"
    )


async def spy_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    if update.effective_user.id != context.bot_data.get("master"):
        await update.effective_message.reply_text("Команда доступна только master.")
        return
    service = _service(context)
    action = context.args[0].lower() if context.args else "status"
    chat_id = update.effective_chat.id
    if action in {"enable", "spawn", "activity"} and update.effective_chat.type not in {
        "group",
        "supergroup",
    }:
        await update.effective_message.reply_text(
            "Эта операция доступна только в group/supergroup."
        )
        return
    if action == "enable":
        result = await service.enable_chat(chat_id)
    elif action == "disable":
        result = await service.disable_chat(chat_id)
        await _remove_event_keyboard(context, chat_id, result.message_id_to_close)
    elif action == "spawn":
        event_type = context.args[1].lower() if len(context.args) > 1 else "recruitment"
        result = await service.manual_spawn(chat_id, event_type=event_type)
        if result.event is not None:
            try:
                message_id = await publish_spy_event(context, result.event)
                await service.attach_message(result.event.event_id, message_id)
                logger.info(
                    "spy_event_created event_id=%s chat_id=%s event_type=%s "
                    "expires_at=%s",
                    result.event.event_id,
                    result.event.chat_id,
                    result.event.event_type,
                    result.event.expires_at.isoformat(),
                )
            except Exception:
                logger.exception(
                    "spy_game: manual publication failed event_id=%s",
                    result.event.event_id,
                )
                await service.cancel_publication(result.event.event_id)
                result = type(result)(False, "Не удалось опубликовать событие.")
    elif action == "activity":
        if len(context.args) > 1:
            requested = ACTIVITY_PROFILE_ALIASES.get(context.args[1].lower())
            if requested is None:
                await update.effective_message.reply_text(
                    "Неизвестный профиль. Используйте calm, balanced или aggressive."
                )
                return
            result = await service.set_activity_profile(chat_id, requested)
            if not result.ok:
                await update.effective_message.reply_text(result.message)
                return
            logger.info(
                "spy_admin_action action=activity profile=%s chat_id=%s "
                "requested_by=%s",
                requested,
                chat_id,
                update.effective_user.id,
            )
        status = await service.get_chat_status(chat_id)
        trigger = service.activity_trigger_settings(status.activity_profile)
        await update.effective_message.reply_text(
            build_activity_admin_text(status, trigger)
        )
        return
    elif action == "status":
        status = await service.get_chat_status(chat_id)
        now = datetime.now(timezone.utc)
        blocks = build_status_blocks(status, now)
        blocks.insert(
            -1,
            {
                "type": "details",
                "summary": "Runtime",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": (
                            "Narrator: "
                            + (
                                "LLM + template fallback"
                                if service.settings.llm_narrator_enabled
                                else "template"
                            )
                            + f"\nActivity profile: {status.activity_profile}"
                        ),
                    }
                ],
            },
        )
        await _send_rich(
            context,
            chat_id,
            blocks,
            fallback_text=(
                f"enabled={status.enabled}, activity={status.activity_score:.1f}, "
                f"profile={status.activity_profile}, next={status.next_event_at}, "
                f"active={status.active_event_id}"
            ),
        )
        return
    else:
        await update.effective_message.reply_text(
            "Использование: /spy_admin enable|disable|spawn "
            "[recruitment|dead_drop|intercept|find_mole|cooperative_operation|chase|"
            "handler|npc|death_operation]|activity "
            "[calm|balanced|aggressive]|status"
        )
        return
    if result.ok:
        logger.info(
            "spy_admin_action action=%s chat_id=%s requested_by=%s",
            action,
            chat_id,
            update.effective_user.id,
        )
    await update.effective_message.reply_text(result.message)
