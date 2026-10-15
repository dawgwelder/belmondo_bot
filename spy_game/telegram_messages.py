"""Compact Telegram messages after an event closes."""
from __future__ import annotations

from functools import partial

from telegram.error import BadRequest

from config import logger


async def _edit_closed_message(edit_text, edit_markup, text: str) -> bool:
    try:
        await edit_text(text=text, reply_markup=None)
        return True
    except BadRequest as error:
        if "message is not modified" in str(error).lower():
            return True
    except Exception:
        pass
    logger.warning("spy_game: could not compact closed event message")
    try:
        await edit_markup(reply_markup=None)
    except Exception:
        logger.warning("spy_game: closed event keyboard remained")
    return False


async def compact_event_message(
    bot, chat_id: int, message_id: int | None, text: str
) -> bool:
    if message_id is None:
        return False
    return await _edit_closed_message(
        partial(bot.edit_message_text, chat_id=chat_id, message_id=message_id),
        partial(bot.edit_message_reply_markup, chat_id=chat_id, message_id=message_id),
        text,
    )


async def compact_event_query(query, text: str) -> bool:
    return await _edit_closed_message(
        query.edit_message_text, query.edit_message_reply_markup, text
    )


async def publish_event_result(
    bot, chat_id: int, message_id: int | None, text: str
) -> None:
    if not await compact_event_message(bot, chat_id, message_id, text):
        await bot.send_message(chat_id=chat_id, text=text)
