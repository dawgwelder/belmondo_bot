"""Compact Telegram messages after an event closes."""
from __future__ import annotations

from functools import partial

from telegram.error import BadRequest

from config import logger


async def _edit_closed_message(edit_text, edit_markup, text: str) -> None:
    try:
        await edit_text(text=text, reply_markup=None)
        return
    except BadRequest as error:
        if "message is not modified" in str(error).lower():
            return
    except Exception:
        pass
    logger.warning("spy_game: could not compact closed event message")
    try:
        await edit_markup(reply_markup=None)
    except Exception:
        logger.warning("spy_game: closed event keyboard remained")


async def compact_event_message(
    bot, chat_id: int, message_id: int | None, text: str
) -> None:
    if message_id is None:
        return
    await _edit_closed_message(
        partial(bot.edit_message_text, chat_id=chat_id, message_id=message_id),
        partial(bot.edit_message_reply_markup, chat_id=chat_id, message_id=message_id),
        text,
    )


async def compact_event_query(query, text: str) -> None:
    await _edit_closed_message(
        query.edit_message_text, query.edit_message_reply_markup, text
    )
