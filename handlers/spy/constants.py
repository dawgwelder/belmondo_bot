"""Spy Clicker Telegram constants."""
from __future__ import annotations


SPY_CALLBACK_PATTERN = r"^spy:[a-z0-9_]{1,32}:[a-z0-9_]{1,24}$"

SPY_HTML5_GAME_PATTERN = r"^[A-Za-z0-9_]{1,64}$"

RECRUITMENT_PROGRESS_MARKER = "📡 ПРОГРЕСС НАБОРА"

ACTIVITY_PROFILE_LABELS = {
    "calm": "спокойный",
    "balanced": "сбалансированный",
    "aggressive": "агрессивный",
}

ACTIVITY_PROFILE_ALIASES = {
    "calm": "calm",
    "balanced": "balanced",
    "aggressive": "aggressive",
    "редко": "calm",
    "норма": "balanced",
    "часто": "aggressive",
}
