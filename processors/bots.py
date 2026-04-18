"""Process messages produced by other bots."""

from telegram import Update
from telegram.ext import ContextTypes

import const
import utils
from config import logger


async def process_bot_messages(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process messages from other bots."""
    if update.message is None or update.message.via_bot is None:
        return

    godnoscop_bot = update.message.via_bot.id == const.GODNOSCOP_ID

    if update.message.via_bot.id not in [
        const.GODNOSCOP_ID,
        const.SELF_ID,
        const.PREDSKAZ_ID,
    ]:
        await utils.sleep_choice_asyncio(
            const.DELAY_CHOICES,
            context.bot,
            update.effective_chat.id,
            update.message.message_id,
            logger,
        )
        logger.info(f"delete_message from shit bot: {update.message.text}")

    elif godnoscop_bot:
        await context.bot.send_message(
            update.effective_chat.id,
            update.message.text.replace("#NoWar", ""),
        )
        await context.bot.delete_message(
            update.effective_chat.id, update.message.message_id
        )
        logger.info(f"edited_message from godnoscop bot: {update.message.text}")
