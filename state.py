"""Mutable, process-wide state container.

Kept separate from `const.py` so constants and prompts stay immutable, and
mutable runtime state has an explicit home.

Two scopes are supported:

* ``vars_dict`` — process-wide settings (owner id, bot mode, pause flag).
  Copied into ``application.bot_data`` on startup.
* ``ensure_chat_state(context)`` — lazily initialises per-chat fields on
  ``context.chat_data`` (conversation history, zavod tracker, rate-limit
  buckets, …) so state no longer leaks across chats.
"""

import asyncio
from collections import deque

from telegram.ext import ContextTypes

from const import SELF_ID, SELF_ID_DEV

vars_dict = {
    "spam_mode": "medium",
    "paused": False,
    "self_id_dev": SELF_ID_DEV,
    "self_id": SELF_ID,
    "master": 113300226,
}


def ensure_chat_state(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Populate ``context.chat_data`` with per-chat default fields lazily.

    Returns the ``chat_data`` mapping itself so callers can use it directly.
    Idempotent — missing keys are added, existing ones are preserved.
    """
    chat_data = context.chat_data
    if chat_data is None:
        return {}

    chat_data.setdefault("chat_deque", deque(maxlen=100))
    chat_data.setdefault("msg_deque", deque(maxlen=100))
    chat_data.setdefault("horoscope_history", deque(maxlen=1))
    chat_data.setdefault("spam_stopper", {})
    chat_data.setdefault("ZAVOD_CHECK", False)
    chat_data.setdefault("dt", None)
    chat_data.setdefault("zavod_text", "")
    chat_data.setdefault("username", None)
    chat_data.setdefault("ai_lock", asyncio.Lock())
    chat_data.setdefault("known_chat_users", {})
    chat_data.setdefault("duels", {})
    chat_data.setdefault("roulette_games", {})
    return chat_data


def remember_chat_user(context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """Remember a chat member so future @username commands can resolve to a user id."""
    if user is None:
        return
    chat_data = ensure_chat_state(context)
    if user.username:
        chat_data["known_chat_users"][user.username.lower()] = user
