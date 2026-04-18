"""Guards: bot pause decorator and master-in-chat membership check with TTL cache."""

import time
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from config import logger

_MASTER_CACHE_TTL_SECONDS = 60
_master_cache: dict[int, tuple[float, bool]] = {}


def pause(func):
    """Skip the wrapped handler when the bot is paused via /pause."""

    @wraps(func)
    async def wrapper(update, context):
        if not context.bot_data.get("paused", False):
            return await func(update, context)
        return None

    return wrapper


async def _is_master_in_chat(
    chat_id: int, master_id: int, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Cached membership check for the master user in a chat."""
    cached = _master_cache.get(chat_id)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _MASTER_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        member = await context.bot.get_chat_member(chat_id, master_id)
        present = member.status in ("member", "administrator", "creator")
    except Exception:
        logger.exception("get_chat_member failed for chat %s", chat_id)
        present = False
    _master_cache[chat_id] = (now, present)
    return present


async def ensure_master_in_chat_for_ai(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """True if master is in the chat; otherwise send refusal and return False."""
    chat_id = update.effective_chat.id
    master_id = context.bot_data["master"]
    if await _is_master_in_chat(chat_id, master_id, context):
        return True
    await context.bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=update.message.message_id if update.message else None,
        text="Я не хочу с тобой разговаривать, mon ami",
        parse_mode="markdown",
    )
    return False
