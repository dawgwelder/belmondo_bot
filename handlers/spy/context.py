"""Spy Clicker Telegram context."""
from __future__ import annotations

from telegram.ext import ContextTypes
from spy_game.narrator import Narrator, TemplateNarrator
from spy_game.service import SpyGameService


def _service(context: ContextTypes.DEFAULT_TYPE) -> SpyGameService:
    service = context.bot_data.get("spy_game")
    if not isinstance(service, SpyGameService):
        raise RuntimeError("Spy Game service is unavailable")
    return service


def _narrator(context: ContextTypes.DEFAULT_TYPE) -> Narrator:
    narrator = context.bot_data.get("spy_narrator")
    return narrator if narrator is not None else TemplateNarrator()
