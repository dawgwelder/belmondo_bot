"""Message-handling entrypoints: parse_message dispatcher, spam/dice cleanup."""

import asyncio
import random

from telegram import Update
from telegram.ext import ContextTypes

import const
import utils
from config import logger
from guards import pause
from handlers.ai import process_ai_response
from if_rules import get_trigger_type, ifs, process_trigger_response
from processors import (
    process_bot_messages,
    process_diarrhea_spell,
    process_jackpot,
    process_media_responses,
    process_men_squad_message,
    process_pot_drinking,
    process_special_commands,
    process_sticker_responses,
    process_zalupa_stickers,
)


async def spam_gif_detector(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Detect and remove rapidly-repeated GIFs from the same user."""
    if update.message.document.mime_type != "video/mp4":
        return
    last_msg = {
        "from": update.message.from_user.id,
        "date": update.message.date,
    }
    context.bot_data["msg_deque"].append(last_msg)

    for idx in range(len(context.bot_data["msg_deque"]) - 1):
        msg = context.bot_data["msg_deque"][idx]
        if (
            msg["from"] == last_msg["from"]
            and (last_msg["date"] - msg["date"]).total_seconds() < 3
        ):
            await context.bot.delete_message(
                update.effective_chat.id, update.message.message_id
            )


@pause
async def parse_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message parsing function — dispatches to processors and trigger engine."""
    bot_data = context.bot_data

    await process_bot_messages(update, context)
    await process_men_squad_message(update, context)
    await process_ai_response(update, context)

    if update.message.text is None:
        return

    msg = utils.clean_string(update.message.text.lower())
    _id = update.message.from_user.id
    ts = update.message.date
    prev_ts = context.bot_data["spam_stopper"].get(_id, None)

    if (
        prev_ts is not None
        and (ts - prev_ts).total_seconds() < 3
        and _id != context.bot_data["master"]
    ):
        msg = False

    context.bot_data["spam_stopper"][_id] = ts

    if not msg:
        return

    text, prob = ifs(msg=msg, _id=_id, spam_mode=bot_data["spam_mode"])
    if text:
        logger.info(f"triggered by: {msg}")
        logger.info(f"scripted answer_message: flag to show was {bool(prob)}")

    if text and prob:
        trigger_type = get_trigger_type(text)
        await process_trigger_response(update, context, text, trigger_type)

        log_text = text
        if len(log_text.split()) > 20:
            log_text = (
                f"{' '.join([log_text.split()[idx] for idx in range(5)])}"
                f"...{' '.join([log_text.split()[idx] for idx in range(-3, 0)])}"
            )
        logger.info(f"scripted answer_message: replied with {log_text}")

    await process_diarrhea_spell(update, context, msg)
    await process_pot_drinking(update, context, msg)
    await process_media_responses(update, context, msg)
    await process_sticker_responses(update, context, msg)
    await process_zalupa_stickers(update, context, msg)
    await process_jackpot(update, context, msg)
    await process_special_commands(update, context, msg)


async def delete_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete dice messages after a short delay."""
    if update.message.dice.emoji in const.emojis:
        await asyncio.sleep(random.choice(const.CHOICES))
        await context.bot.delete_message(
            update.effective_chat.id, update.message.message_id
        )
        logger.info(f"delete_dice: msg_id={update.message.message_id}")
