"""Simple commands: /quote, /roll, /pause."""

import random

from telegram import Update
from telegram.ext import ContextTypes

import utils
from config import logger
from guards import pause


@pause
async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a random quote."""
    text = utils.quote_choice()
    logger.info(f"quote: {text[:10]}...")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


@pause
async def roll_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll a dice."""
    await context.bot.send_dice(
        chat_id=update.effective_message.chat_id,
        reply_to_message_id=update.message.message_id,
    )
    logger.info("roll_dice: success")


async def paused(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle bot pause state (master-only)."""
    if update.message.from_user.id == context.bot_data["master"]:
        context.bot_data["paused"] = not context.bot_data.get("paused", False)
        if context.bot_data["paused"]:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Бельмондо спит"
            )
    else:
        if random.randint(0, 10) == 10:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ты чёт ошибся, другалек, я только по команде хозяина сплю",
            )
