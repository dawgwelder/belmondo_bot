"""Spy Clicker Telegram callback recruitment."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.models import ClaimStatus
from spy_game.settings import AGENT_TYPES
from .context import _service
from .formatting import _display_name
from .publication import _edit_recruitment_progress


async def handle_claim(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        result = await service.claim_event(
            event_id=value,
            action="claim",
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: claim failed event_id=%s", value)
        await query.answer(
            "Связь с центром потеряна. Попробуйте ещё раз.", show_alert=True
        )
        return

    if result.status is ClaimStatus.WON:
        agent = AGENT_TYPES[result.reward.agent_type]
        await query.answer(
            f"Контакт {result.claims}/{result.required_claims}: "
            f"{agent.display_name} ×{result.reward.amount}",
            show_alert=True,
        )
        await _edit_recruitment_progress(context, query, service, value)
        logger.info(
            "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
            "reward_id=%s reward_amount=%s",
            result.event_id,
            chat.id,
            user.id,
            result.reward.agent_type,
            result.reward.amount,
        )
    elif result.status is ClaimStatus.ALREADY_CLAIMED:
        await query.answer(
            "Вы уже получили агента в этом наборе.",
            show_alert=True,
        )
    elif result.status is ClaimStatus.EXPIRED:
        await query.answer("Окно контакта уже закрылось.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    elif result.status is ClaimStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент оказался быстрее.", show_alert=True)
    elif result.status is ClaimStatus.DISABLED:
        await query.answer("Разведсеть сейчас отключена.", show_alert=True)
    else:
        await query.answer("Этот сигнал больше недействителен.", show_alert=True)
