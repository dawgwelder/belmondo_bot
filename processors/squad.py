"""Process special messages from the men squad (mostly the 'нахуй баб' chant)."""

import asyncio
import random
import re

from telegram import Update
from telegram.ext import ContextTypes

import const


async def process_men_squad_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process special messages from men squad."""
    first_condition = (
        update.message is not None and update.message.from_user is not None
    )
    if not (
        first_condition
        and update.message.from_user.id in const.men_squad
        and "нахуй баб" in update.message.text.lower()
    ):
        return

    regex = r"(-?[0-9]|[1-9][0-9]|[1-9][0-9][0-9])"
    numbers = re.findall(regex, update.message.text)

    try:
        _cast = int(numbers[0]) if numbers else None
    except ValueError:
        _cast = None

    if _cast is None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="Ты неправильно накастовал, дебил",
            parse_mode="markdown",
        )
        return

    count = min(abs(_cast), 10)
    for _ in range(count):
        text = random.choice(["НАХУЙ БАБ", "_НАХУЙ БАБ_", "*НАХУЙ БАБ*"])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="markdown",
        )
        await asyncio.sleep(random.choice([0.5, 0.25, 1, 0.75, 0.666]))
