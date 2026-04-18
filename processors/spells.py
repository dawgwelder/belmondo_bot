"""Spell-like text processors: diarrhea spell, pot drinking, dembel/amir countdowns, dice."""

import asyncio
import datetime
import random
import re

from telegram import Update
from telegram.ext import ContextTypes

import const
import utils
from config import tz


async def process_special_commands(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process special command patterns in messages."""
    if "дембель" in msg:
        td = datetime.datetime(2028, 11, 14, tzinfo=tz) - datetime.datetime.now(tz)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"Арбузу до пенсии осталось ровно {utils.td_convert(td)}",
            parse_mode="markdown",
        )

    if "амир" in msg:
        td = datetime.datetime(2026, 10, 14, tzinfo=tz) - datetime.datetime.now(tz)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=f"Амиру до свободы осталось {utils.td_convert(td)}",
            parse_mode="markdown",
        )

    if "страшно жить" in msg:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="ВАЩЕ ПИЗДЕЦ",
            parse_mode="markdown",
        )

    if "кубик" in msg:
        text = utils.roll_custom_dice(msg)
        if text is None:
            return
        if text == "default":
            await context.bot.send_dice(
                chat_id=update.effective_message.chat_id,
                reply_to_message_id=update.message.message_id,
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text=text,
                parse_mode="markdown",
            )


async def process_diarrhea_spell(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process the diarrhea spell command."""
    if not (msg.startswith("понос ") and " на " in msg):
        return

    user = msg.split("понос ")[-1].split(" на")[0]
    reg_value = re.sub("[^0-9]", "", msg)
    reg_value = int(reg_value) if reg_value else -999
    value = msg[-1]
    text = "Вы допустили ошибку в заклинании - теперь ждите кару самопоноса"

    if value.isdigit():
        value = int(value)
        if 1 <= value <= 6 and reg_value == value:
            roll = await context.bot.send_dice(chat_id=update.effective_message.chat_id)
            await asyncio.sleep(2.7)
            if roll.dice.value == value:
                text = f"*Понос* {user} обеспечен"
            else:
                text = "_Каст поноса был провален!_"

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=text,
        parse_mode="markdown",
    )


async def process_pot_drinking(
    update: Update, context: ContextTypes.DEFAULT_TYPE, msg: str
) -> None:
    """Process pot drinking status messages."""
    if not any(
        phrase in msg
        for phrase in ["горшок не пьет", "горшок не пьёт", "горшок держится"]
    ):
        return

    not_drink_choice = random.choice(
        ["не пьет", "держится", "в завязке", "не бухает", "проявляет силу воли"]
    )

    not_drink = datetime.datetime.now(tz).date() - const.POT_DATE
    not_drink_ending = utils.td_convert(not_drink)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text=f"Горшок {not_drink_choice} уже {not_drink_ending}",
        parse_mode="markdown",
    )
