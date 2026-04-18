"""Message processors split by domain (bots, squad, spells, media)."""

from processors.bots import process_bot_messages
from processors.squad import process_men_squad_message
from processors.spells import (
    process_diarrhea_spell,
    process_pot_drinking,
    process_special_commands,
)
from processors.media import (
    process_jackpot,
    process_media_responses,
    process_sticker_responses,
    process_zalupa_stickers,
)

__all__ = [
    "process_bot_messages",
    "process_men_squad_message",
    "process_diarrhea_spell",
    "process_pot_drinking",
    "process_special_commands",
    "process_jackpot",
    "process_media_responses",
    "process_sticker_responses",
    "process_zalupa_stickers",
]
