"""Spy Clicker Telegram transport."""
from __future__ import annotations

from telegram import InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import logger
from telegram_utils import send_rich_message
from .cleanup import schedule_menu_cleanup


def _markup_payload(markup: InlineKeyboardMarkup | None) -> dict | None:
    if markup is None:
        return None
    return markup.to_dict()


async def _send_rich(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    blocks: list[dict],
    *,
    fallback_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    try:
        payload = await send_rich_message(
            context.bot.token,
            chat_id,
            blocks,
            reply_markup=_markup_payload(reply_markup),
        )
        return int(payload["result"]["message_id"])
    except Exception:
        logger.exception("spy_game: sendRichMessage failed chat_id=%s", chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
            reply_markup=reply_markup,
        )
        return message.message_id


async def _send_temporary_rich(
    context, chat, blocks, *, fallback_text, reply_markup=None
) -> int:
    message_id = await _send_rich(
        context, chat.id, blocks, fallback_text=fallback_text, reply_markup=reply_markup
    )
    schedule_menu_cleanup(context, chat, message_id)
    return message_id


async def _remove_event_keyboard(context, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    except Exception:
        logger.warning(
            "spy_game: failed to remove keyboard chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )
