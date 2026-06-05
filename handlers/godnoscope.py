"""Handlers for the /horoscope, /horoscope_mail commands and the inline-keyboard callback."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import config, logger
from godnoscop.godnoscop_tracker import GodnoscopTracker
from guards import pause
from horoscope import generate_post

tracker = GodnoscopTracker(config)


@pause
async def get_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send pre-generated horoscope posts to the chat."""
    first_post, second_post = await generate_post()
    logger.info("sending horoscopes")
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=first_post
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=second_post
    )


@pause
async def godnoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show horoscope sign selection keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("Овен", callback_data="ОВЕН"),
            InlineKeyboardButton("Телец", callback_data="ТЕЛЕЦ"),
            InlineKeyboardButton("Близнецы", callback_data="БЛИЗНЕЦЫ"),
        ],
        [
            InlineKeyboardButton("Рак", callback_data="РАК"),
            InlineKeyboardButton("Лев", callback_data="ЛЕВ"),
            InlineKeyboardButton("Дева", callback_data="ДЕВА"),
        ],
        [
            InlineKeyboardButton("Весы", callback_data="ВЕСЫ"),
            InlineKeyboardButton("Скорпион", callback_data="СКОРПИОН"),
            InlineKeyboardButton("Стрелец", callback_data="СТРЕЛЕЦ"),
        ],
        [
            InlineKeyboardButton("Козерог", callback_data="КОЗЕРОГ"),
            InlineKeyboardButton("Водолей", callback_data="ВОДОЛЕЙ"),
            InlineKeyboardButton("Рыбы", callback_data="РЫБЫ"),
        ],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выбирай:", reply_markup=reply_markup)


@pause
async def button_godnoscope(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle horoscope sign selection from the inline keyboard."""
    query = update.callback_query
    await query.answer()

    try:
        message = await tracker.get_horoscope(query.data)
    except Exception:
        logger.exception(
            "button_godnoscope: failed to fetch horoscope for sign=%s", query.data
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось получить гороскоп. Попробуйте позже.",
        )
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=message
    )
