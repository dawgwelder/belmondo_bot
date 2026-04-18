"""Application bootstrap: build the Telegram Application and register handlers."""

import asyncio
from collections import deque

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import logger
from handlers.ai import (
    ai_horoscope,
    clear_context,
    magic_prediction,
    tarot,
)
from handlers.commands import paused, quote, roll_dice
from handlers.content import (
    send_goblin,
    send_morning,
    send_oxxxy,
    show_day,
    show_holidays,
)
from handlers.godnoscope import button_godnoscope, get_horoscope, godnoscope
from handlers.messages import delete_dice, parse_message, spam_gif_detector
from state import vars_dict


def _build_handlers() -> list:
    return [
        CommandHandler("quote", quote),
        CommandHandler("goblin", send_goblin),
        CommandHandler("oxxxy", send_oxxxy),
        CommandHandler("day", show_day),
        CommandHandler("holiday", show_holidays),
        CommandHandler("zavod", send_morning),
        CommandHandler("roll", roll_dice),
        CommandHandler("horoscope", godnoscope),
        CommandHandler("horoscope_mail", get_horoscope),
        CommandHandler("pause", paused),
        CommandHandler("ai_horoscope", ai_horoscope),
        CommandHandler("clear_context", clear_context),
        CommandHandler("tarot", tarot),
        CommandHandler("magic_prediction", magic_prediction),
    ]


async def main(
    mode: str = "dev", spam_mode: str = "medium", token: str = None
) -> None:
    """Initialize and run the bot."""
    application = None
    vars_dict["spam_mode"] = spam_mode
    vars_dict["chat_deque"] = deque(maxlen=100)
    vars_dict["msg_deque"] = deque(maxlen=100)
    vars_dict["horoscope_history"] = deque(maxlen=1)

    if mode not in ["dev", "prod"]:
        logger.error("Bot start: FAIL! Invalid mode")
        return

    if mode == "dev":
        vars_dict["self_id"] = vars_dict["self_id_dev"]

    application = (
        Application.builder()
        .token(token)
        .read_timeout(1000)
        .connect_timeout(1000)
        .build()
    )

    logger.info("Bot start: success!")

    application.bot_data.update(vars_dict)

    for handler in _build_handlers():
        application.add_handler(handler)

    application.add_handler(MessageHandler(filters.Dice.ALL, delete_dice))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message)
    )
    application.add_handler(MessageHandler(filters.Document.ALL, spam_gif_detector))

    application.add_handler(CallbackQueryHandler(button_godnoscope))

    logger.info("Bot is running... Press Ctrl+C to stop")

    try:
        await application.initialize()
        await application.start()

        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass

    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        try:
            await application.stop()
            await application.shutdown()
            logger.info("Bot shutdown complete")
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")


def run_bot(
    mode: str = "dev", spam_mode: str = "medium", token: str = None
) -> None:
    """Wrapper that runs the async main function inside asyncio.run."""
    try:
        asyncio.run(main(mode, spam_mode, token))
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback

        traceback.print_exc()
