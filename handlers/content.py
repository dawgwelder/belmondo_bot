"""Content commands: /oxxxy, /goblin, /zavod, /day, /holiday."""

import datetime
import os
import random

import aiofiles
import pandas as pd
from telegram import Update
from telegram.ext import ContextTypes

import const
from config import logger, tz
from guards import pause
from oxxxy_urls import oxxxy_playlist
from site_parser import get_holidays
from state import ensure_chat_state


@pause
async def send_oxxxy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random Oxxxy mashup URL."""
    url = random.choice(oxxxy_playlist)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=url,
        parse_mode="markdown",
    )
    logger.info(f"send_oxxxy: oxxy mashup {url} sent")


@pause
async def send_goblin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random goblin content."""
    goblin_dir = "img/goblin/"
    mode = random.choice(["mp4", "img", "sticker", "text", "youtube"])
    urls = const.goblin_urls

    try:
        if mode == "mp4":
            animation = os.path.join(
                goblin_dir,
                random.choice(
                    [f for f in os.listdir(goblin_dir) if f.endswith(".mp4")]
                ),
            )
            async with aiofiles.open(animation, "rb") as f:
                animation_data = await f.read()
                await context.bot.send_animation(
                    chat_id=update.effective_chat.id,
                    animation=animation_data,
                    read_timeout=20,
                    reply_to_message_id=update.message.message_id,
                )
                logger.info(f"send_goblin: mode {mode} file {animation} sent")

        elif mode == "img":
            img = os.path.join(
                goblin_dir,
                random.choice(
                    [f for f in os.listdir(goblin_dir) if f.endswith(".jpeg")]
                ),
            )
            async with aiofiles.open(img, "rb") as f:
                photo_data = await f.read()
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    photo=photo_data,
                )
                logger.info(f"send_goblin: mode {mode} file {img} sent")

        elif mode == "sticker":
            sticker = os.path.join(
                goblin_dir,
                random.choice(
                    [f for f in os.listdir(goblin_dir) if f.endswith(".webp")]
                ),
            )
            async with aiofiles.open(sticker, "rb") as f:
                sticker_data = await f.read()
                await context.bot.send_sticker(
                    chat_id=update.effective_chat.id, sticker=sticker_data
                )
                logger.info(f"send_goblin: mode {mode} file {sticker} sent")

        elif mode == "text":
            text = random.choice(const.goblin_pasta)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )
            logger.info(f"send_goblin: mode {mode} file text sent")

        elif mode == "youtube":
            url = random.choice(urls)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=f"СМОТРЕТЬ ВСЕМ\n{url}",
                parse_mode="markdown",
            )
            logger.info(f"send_goblin: mode {mode} file {url} sent")

    except FileNotFoundError as e:
        logger.error(f"Goblin file not found: {e}")
    except Exception:
        logger.exception("Error sending goblin content")


@pause
async def send_morning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send morning factory/office message and pick zavodchanin of the day."""
    chat_data = ensure_chat_state(context)

    text = "Русские, в офис / на завод!\n..._loading_..."
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=text,
        parse_mode="markdown",
    )
    logger.info("send_morning: preload")

    username = update.effective_user.username

    if chat_data["dt"] is None:
        chat_data["dt"] = datetime.datetime.now()
        chat_data["ZAVOD_CHECK"] = True
        chat_data["username"] = username
    else:
        chat_data["ZAVOD_CHECK"] = (
            (datetime.datetime.now() - chat_data["dt"]).days > 0
            and (4 <= datetime.datetime.now().hour < 12)
        )
        if chat_data["ZAVOD_CHECK"]:
            chat_data["username"] = username

    if chat_data["ZAVOD_CHECK"]:
        file = random.choice(
            ["img/zavodchanin.jpeg", "img/zombie_zavod.jpeg", "img/flower.jpeg"]
        )
        try:
            async with aiofiles.open(file, "rb") as f:
                photo_data = await f.read()
                zavod_user = f"Офисчанин/Заводчанин дня - @{username}!"
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    reply_to_message_id=update.message.message_id,
                    caption=zavod_user,
                    photo=photo_data,
                )
                logger.info("send_morning: zavod success!")
        except FileNotFoundError:
            logger.error(f"Zavod image not found: {file}")
    else:
        raw_name = chat_data.get("username") or username or "без_ника"
        zavod_user = raw_name.replace("@", "")
        text = (
            f"Поздно, другалёчек!\n"
            f"Офисчанин/Заводчанин дня - @{zavod_user}!"
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
        logger.info("send_morning: zavod success but late!")


@pause
async def show_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show day-specific sticker."""
    weekday = pd.Timestamp(datetime.datetime.now(tz)).weekday()
    sticker = os.path.join("img/eva", f"{weekday}.webp")

    try:
        async with aiofiles.open(sticker, "rb") as f:
            sticker_data = await f.read()
            await context.bot.send_sticker(
                chat_id=update.effective_chat.id, sticker=sticker_data
            )
            logger.info(f"show_day: file {sticker} sent")
    except FileNotFoundError:
        logger.error(f"Day sticker not found: {sticker}")


@pause
async def show_holidays(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current holidays."""
    dt = datetime.datetime.now(tz)
    text = get_holidays(dt)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="markdown",
    )
    logger.info("show_day: sent holidays list")
