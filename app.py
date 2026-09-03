"""Application bootstrap: build the Telegram Application and register handlers."""

import asyncio

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
    MAGIC_PREDICTION_CALLBACK,
    ai_horoscope,
    clear_context,
    load_tarot_deck,
    magic_prediction,
    magic_prediction_callback,
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
from handlers.duel import DUEL_CALLBACK_PATTERN, duel, duel_callback, duel_cancel
from handlers.games import (
    GAME_CALLBACK_PATTERN,
    game,
    game_callback,
    game_cancel,
)
from handlers.godnoscope import button_godnoscope, get_horoscope, godnoscope
from handlers.messages import delete_dice, parse_message, spam_gif_detector
from handlers.roulette import ROULETTE_CALLBACK_PATTERN, roulette_callback
from handlers.spy_game import (
    SPY_CALLBACK_PATTERN,
    spy_admin,
    spy_callback,
    spy_game_tick,
    spy_menu,
    track_spy_activity,
)
from handlers.tldr import tldr
from horoscope import close_horoscope_http_client
from spy_game import SpyGameService, SpySettings
from spy_game.narrator import build_narrator
from spy_game.webapp import SpyWebAppServer, SpyWebAppSettings
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
        CommandHandler("tldr", tldr),
        CommandHandler("duel", duel),
        CommandHandler("duel_cancel", duel_cancel),
        CommandHandler("game", game),
        CommandHandler("game_cancel", game_cancel),
        CommandHandler("spy", spy_menu),
        CommandHandler("spy_admin", spy_admin),
    ]


async def main(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Initialize and run the bot."""
    application = None
    spy_service = None
    spy_webapp = None
    vars_dict["spam_mode"] = spam_mode

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

    spy_settings = SpySettings.from_env(mode)
    spy_service = SpyGameService(spy_settings)
    try:
        await spy_service.initialize()
    except Exception:
        await spy_service.close()
        raise
    try:
        application.bot_data["spy_game"] = spy_service
        application.bot_data["spy_narrator"] = build_narrator(
            spy_settings,
            spy_service.database,
        )
        spy_webapp_settings = SpyWebAppSettings.from_env()
        if spy_webapp_settings.enabled:
            spy_webapp = SpyWebAppServer(
                spy_service,
                token,
                spy_webapp_settings,
            )
            application.bot_data["spy_webapp"] = spy_webapp
    except Exception:
        await spy_service.close()
        raise
    logger.info(
        "Spy game initialized: enabled=%s director=%s narrator=%s webapp=%s "
        "mode=%s allowed_chats=%s db=%s",
        spy_settings.enabled,
        spy_settings.llm_director_enabled,
        spy_settings.llm_narrator_enabled,
        spy_webapp_settings.enabled,
        mode,
        len(spy_settings.allowed_chat_ids),
        spy_settings.database_path,
    )

    try:
        application.bot_data["tarot_deck"] = load_tarot_deck()
        logger.info(
            "Bot start: loaded tarot deck (%s cards)",
            len(application.bot_data["tarot_deck"]),
        )
    except Exception:
        logger.exception("Bot start: failed to load tarot deck")
        application.bot_data["tarot_deck"] = []

    for handler in _build_handlers():
        application.add_handler(handler)

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, track_spy_activity),
        group=-1,
    )

    application.add_handler(MessageHandler(filters.Dice.ALL, delete_dice))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message)
    )
    media_spam_filter = (
        filters.Document.GIF
        | filters.Document.MP4
        | filters.ANIMATION
        | filters.Sticker.ALL
    )
    application.add_handler(MessageHandler(media_spam_filter, spam_gif_detector))

    application.add_handler(
        CallbackQueryHandler(duel_callback, pattern=DUEL_CALLBACK_PATTERN)
    )
    application.add_handler(
        CallbackQueryHandler(game_callback, pattern=GAME_CALLBACK_PATTERN)
    )
    application.add_handler(
        CallbackQueryHandler(roulette_callback, pattern=ROULETTE_CALLBACK_PATTERN)
    )
    application.add_handler(
        CallbackQueryHandler(spy_callback, pattern=SPY_CALLBACK_PATTERN)
    )
    application.add_handler(
        CallbackQueryHandler(
            magic_prediction_callback, pattern=f"^{MAGIC_PREDICTION_CALLBACK}$"
        )
    )
    application.add_handler(CallbackQueryHandler(button_godnoscope))

    if spy_settings.enabled:
        if application.job_queue is None:
            await spy_service.close()
            raise RuntimeError("Spy Game requires python-telegram-bot JobQueue")
        application.job_queue.run_repeating(
            spy_game_tick,
            interval=spy_settings.tick_seconds,
            first=1,
            name="spy-game-tick",
            job_kwargs={"max_instances": 1, "coalesce": True},
        )

    logger.info("Bot is running... Press Ctrl+C to stop")

    try:
        await application.initialize()
        await application.start()
        if spy_webapp is not None:
            await spy_webapp.start()

        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass

    except KeyboardInterrupt:
        logger.info("Shutting down bot...")
    finally:
        if spy_webapp is not None:
            try:
                await spy_webapp.close()
            except Exception:
                logger.exception("Error while stopping Spy Game Web App")
        try:
            await application.stop()
        except Exception:
            logger.exception("Error while stopping bot")
        try:
            await close_horoscope_http_client()
            await application.shutdown()
            logger.info("Bot shutdown complete")
        except Exception:
            logger.exception("Error during shutdown")
        finally:
            if spy_service is not None:
                await spy_service.close()


def run_bot(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Wrapper that runs the async main function inside asyncio.run."""
    try:
        asyncio.run(main(mode, spam_mode, token))
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception:
        logger.exception("Unexpected error in run_bot")
