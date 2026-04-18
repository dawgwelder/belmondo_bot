"""Media (photo/sticker) responses to specific keywords in text messages."""

import random
import re

import aiofiles
from telegram import Update
from telegram.ext import ContextTypes

import const
from config import logger


def _should_send_cola(msg: str) -> bool:
    return (
        "колокол" not in msg.split()
        and "колокольн" not in msg
        and "колокол" in msg
    )


def _should_send_elephant(msg: str) -> bool:
    return (
        "слон" in msg
        and msg.startswith("слон")
        and not msg.startswith("слонн")
        and not msg.startswith("прислон")
        and random.random() < 0.5
    )


async def process_media_responses(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process media responses based on message content."""
    if _should_send_cola(msg):
        reply_to = (
            update.message.reply_to_message.message_id
            if update.message.reply_to_message
            else update.message.message_id
        )
        try:
            async with aiofiles.open("img/colocola.jpg", "rb") as f:
                photo_data = await f.read()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=reply_to,
                    caption=const.colocola,
                    photo=photo_data,
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error("Cola image not found")

    if _should_send_elephant(msg):
        reply_to = (
            update.message.reply_to_message.message_id
            if update.message.reply_to_message
            else update.message.message_id
        )
        try:
            async with aiofiles.open("img/slon.jpg", "rb") as f:
                photo_data = await f.read()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=reply_to,
                    photo=photo_data,
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error("Elephant image not found")

    if "нацист" in msg:
        try:
            file = random.choice(["img/nz.jpg", "img/nz_1.jpg"])
            async with aiofiles.open(file, "rb") as f:
                photo_data = await f.read()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    photo=photo_data,
                    parse_mode="markdown",
                )
        except FileNotFoundError:
            logger.error("Nazi image not found")


async def process_sticker_responses(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process sticker responses based on message content."""
    if "любителям синтетики" in msg:
        try:
            async with aiofiles.open("img/GM.webp", "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=sticker_data
                )
                logger.info("answer_message: sticker sent")
        except FileNotFoundError:
            logger.error("GM sticker not found")

    if msg == "вот так вот":
        try:
            async with aiofiles.open("img/nevsky.jpeg", "rb") as f:
                photo_data = await f.read()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    photo=photo_data,
                )
                logger.info("answer_message: nevsky photo sent")
        except FileNotFoundError:
            logger.error("Nevsky image not found")

    if msg == "доброе утро":
        try:
            async with aiofiles.open("img/GM_SHUE.webp", "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=sticker_data
                )
                logger.info("answer_message: good morning crackheads sticker sent")
        except FileNotFoundError:
            logger.error("GM_SHUE sticker not found")

    if "хуяндекс" in msg:
        try:
            async with aiofiles.open("img/yandex.webp", "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=sticker_data
                )
        except FileNotFoundError:
            logger.error("Yandex sticker not found")

    if re.search(
        r"\b(?:спокойной?|доброй?|сладкой?|ой)\s+(?:ночи|ночки|ночью)\b",
        msg,
        re.IGNORECASE,
    ):
        try:
            async with aiofiles.open("img/GN.webp", "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=sticker_data
                )
                logger.info("answer_message: yandex sticker sent")

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    text=random.choice(
                        [
                            "Good night!",
                            "Спокойной ночи",
                            "Сладких снов",
                            "Покасики!",
                        ]
                    ),
                    parse_mode="markdown",
                )
                logger.info("answer_message: good night crackheads sticker sent")
        except FileNotFoundError:
            logger.error("GN sticker not found")


async def process_zalupa_stickers(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process zalupa sticker responses."""
    if "залуп" not in msg:
        return
    file = random.choice(["img/zalupa.webp", "img/zalupa_1.webp"])
    try:
        async with aiofiles.open(file, "rb") as f:
            sticker_data = await f.read()
            await context.bot.send_sticker(
                chat_id=update.effective_chat.id, sticker=sticker_data
            )
            logger.info("answer_message: zalupa sticker sent")
    except FileNotFoundError:
        logger.error(f"Zalupa sticker not found: {file}")


async def process_jackpot(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process jackpot responses."""
    if "джекпот" not in msg:
        return
    try:
        async with aiofiles.open("img/jackpot.webp", "rb") as f:
            sticker_data = await f.read()
            await context.bot.send_sticker(
                chat_id=update.effective_chat.id, sticker=sticker_data
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=random.choice(["*ДЖЕКПОТ!*", "Джекпот! Хуй те в рот!"]),
                parse_mode="markdown",
            )
            logger.info("answer_message: jackpot sticker sent")
    except FileNotFoundError:
        logger.error("Jackpot sticker not found")
