import random
import re
import asyncio

from collections import deque

import fire
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)

from if_rules import ifs, process_trigger_response, get_trigger_type
import const
import utils
from config import client, config, logger, tz, TELEGRAM_MAX_MESSAGE_LENGTH
from state import vars_dict
from guards import pause, ensure_master_in_chat_for_ai
from telegram_utils import parse_stream, send_long_message
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
from handlers.content import (
    send_goblin,
    send_morning,
    send_oxxxy,
    show_day,
    show_holidays,
)
from handlers.ai import (
    ai_horoscope,
    clear_context,
    magic_prediction,
    process_ai_response,
    tarot,
)
from handlers.commands import paused, quote, roll_dice
from handlers.godnoscope import button_godnoscope, get_horoscope, godnoscope
from handlers.messages import delete_dice, parse_message, spam_gif_detector

async def main(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Main bot initialization and setup - now fully async."""
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

    # Create Application instead of Updater
    application = (
        Application.builder()
        .token(token)
        .read_timeout(1000)
        .connect_timeout(1000)
        .build()
    )
        
    logger.info("Bot start: success!")
    
    # Update bot_data
    application.bot_data.update(vars_dict)
        
    # Register command handlers
    handlers = [
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
        
    for handler in handlers:
        application.add_handler(handler)
    
    # Register message handlers (note: filters instead of Filters)
    application.add_handler(MessageHandler(filters.Dice.ALL, delete_dice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, parse_message))
    application.add_handler(MessageHandler(filters.Document.ALL, spam_gif_detector))
        
    # Register callback handlers
    application.add_handler(CallbackQueryHandler(button_godnoscope))
    
    # Start the application with polling
    logger.info("Bot is running... Press Ctrl+C to stop")
    
    try:
        # Initialize and start the application
        await application.initialize()
        await application.start()
        
        # Run polling in the current event loop
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
        # Keep the bot running
        try:
            # Wait indefinitely
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
        


def run_bot(mode: str = "dev", spam_mode: str = "medium", token: str = None) -> None:
    """Wrapper function to run the async main function."""
    try:
        asyncio.run(main(mode, spam_mode, token))
    except KeyboardInterrupt:
        logger.info("Bot stopped by KeyboardInterrupt")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    fire.Fire(run_bot)
