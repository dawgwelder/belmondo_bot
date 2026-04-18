import random
import re
import datetime
import asyncio

from collections import deque

import fire
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from horoscope import generate_post, build_ai_horoscope_user_message, generate_tarot_prompt
from godnoscop.godnoscop_tracker import GodnoscopTracker

tracker = GodnoscopTracker(config)


async def process_ai_response(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process AI chat responses."""
    if (update.message is not None and
        update.message.reply_to_message is not None and
        update.message.reply_to_message.from_user.id == context.bot_data["self_id"] and
        update.message.from_user.id not in const.excluded_uids):

        if not await ensure_master_in_chat_for_ai(update, context):
            return

        content = update.message.text
        model_type = "deepseek-reasoner" if content.lower().startswith("подумай") else "deepseek-chat"

        context.bot_data["chat_deque"].append({"role": "system", "content": const.professional_prompt})
        context.bot_data["chat_deque"].append({"role": "user", "content": content})

        try:
            text = ""
            if not "гороскоп" in content:
                stream = await client.chat.completions.create(
                    model=model_type,
                    messages=list(context.bot_data["chat_deque"]),
                    stream=True
                )
                text = await parse_stream(stream)
            else:
                stream = await client.chat.completions.create(
                    model=model_type,
                    messages=list([{"role": "system", "content": const.professional_prompt},
                                   {"role": "user", "content": content}]),
                    stream=True
                )
                text = await parse_stream(stream)

            context.bot_data["chat_deque"].append({"role": "assistant", "content": text})

            await send_long_message(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="markdown",
                reply_to_message_id=update.message.message_id
            )
            logger.info(f"chatGPT: generated text sent text:{text}")

        except Exception as e:
            logger.error(f"Error generating AI response: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text="Извините, произошла ошибка при генерации ответа.",
                parse_mode="markdown",
            )


@pause
async def ai_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send AI horoscope."""
    if not await ensure_master_in_chat_for_ai(update, context):
        return
    if update.effective_user.id not in const.excluded_uids:
        history = list(context.bot_data["horoscope_history"]) if context.bot_data["horoscope_history"] else None
        prompt = build_ai_horoscope_user_message(history)
        messages = [{"role": "system", "content": const.professional_prompt_ai_horoscope},
                    {"role": "user", "content": prompt}]
        if context.bot_data["horoscope_history"]:
            previous_messages = []
            for message in context.bot_data["horoscope_history"]:
                previous_messages.append({"role": "assistant", "content": message})
            messages = previous_messages + messages

        text = ""
        try:
            stream = await client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                stream=True
            )
            text = await parse_stream(stream)
        except Exception as e:
            logger.error(f"ai_horoscope: API error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Не удалось сгенерировать гороскоп. Попробуйте позже.",
                parse_mode="markdown",
            )
            return
        if text:
            context.bot_data["horoscope_history"].append(text)
            await send_long_message(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="markdown"
            )
        logger.info(
            "ai_horoscope: user_message_len=%s horoscope_history_count=%s",
            len(prompt),
            len(context.bot_data["horoscope_history"]),
        )


@pause
async def tarot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_master_in_chat_for_ai(update, context):
        return
    
    result = []
    with open("tarot_cards.json") as f:
        deck = random.sample(json.load(f)["cards"], k=3)
    for card, time in zip(deck, ["прощлое", "настоящее", "будущее"]):
        reversed_flag = random.choice([True, False])
        result.append({
            "card": card["name"],
            "orientation": "перевернутая" if reversed_flag else "прямая",
            "meaning": card["reversed_meaning"] if reversed_flag else card["upright_meaning"],
            "time": time
        })
    tarot_prompt = generate_tarot_prompt(result)
    
    request = []
    request.append({"role": "system", "content": const.professional_prompt})
    request.append({"role": "user", "content": tarot_prompt})
    
    stream = await client.chat.completions.create(
                        model="deepseek-reasoner",
                        messages=request,
                        stream=True
                    )
    text = await parse_stream(stream)
    
    await send_long_message(
                    bot=context.bot,
                    chat_id=update.effective_chat.id,
                    text=text,
                    parse_mode="markdown",
                    reply_to_message_id=update.message.message_id
                )


_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


@pause
async def magic_prediction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Шуточное предсказание на день (1–2 предложения)."""
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    now = datetime.datetime.now(tz)
    weekday_ru = _WEEKDAYS_RU[now.weekday()]
    date_msk = now.strftime("%d.%m.%Y")
    user_content = (
        f"Сегодня по Москве: {weekday_ru}, {date_msk}.\n\n"
        "Дай шуточное предсказание на этот день: ровно одно или два предложения — "
        "намекни, как может пройти день, чего опасаться или к чему стремиться. "
        "Без списков и без длинных абзацев; заверши короткой французской фразой с переводом в скобках."
    )
    messages = [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        stream = await client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            stream=True,
            max_tokens=180,
        )
        text = await parse_stream(stream)
    except Exception as e:
        logger.error(f"magic_prediction: API error: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="Не удалось сгенерировать предсказание. Попробуйте позже.",
            parse_mode="markdown",
        )
        return

    if not (text or "").strip():
        logger.warning("magic_prediction: empty model response")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="Пустой ответ модели. Попробуйте ещё раз.",
            parse_mode="markdown",
        )
        return

    await send_long_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="markdown",
        reply_to_message_id=update.message.message_id,
    )
    logger.info("magic_prediction: sent prediction for %s", date_msk)


@pause
async def quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send random quote."""
    text = utils.quote_choice()
    logger.info(f"quote: {text[:10]}...")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


@pause
async def get_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send horoscope posts."""
    first_post, second_post = generate_post()
    logger.info("sending horoscopes")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=first_post)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=second_post)


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
async def button_godnoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle horoscope sign selection."""
    query = update.callback_query
    await query.answer()
    
    message = await tracker.get_horoscope(query.data)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=message)


async def spam_gif_detector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect and remove spam GIFs."""
    if update.message.document.mime_type == "video/mp4":
        last_msg = {
            "from": update.message.from_user.id,
            "date": update.message.date
        }
        context.bot_data["msg_deque"].append(last_msg)
        
        for idx in range(len(context.bot_data["msg_deque"]) - 1):
            msg = context.bot_data["msg_deque"][idx]
            if (msg["from"] == last_msg["from"] and
                (last_msg["date"] - msg["date"]).total_seconds() < 3):
                await context.bot.delete_message(
                    update.effective_chat.id, update.message.message_id
                )


@pause
async def parse_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main message parsing function - now fully async."""
    bot_data = context.bot_data
    text = ""
    prob = 0

    await process_bot_messages(update, context)
    await process_men_squad_message(update, context)
    await process_ai_response(update, context)

    if update.message.text is not None and not text:
        msg = utils.clean_string(update.message.text.lower())
        _id = update.message.from_user.id
        ts = update.message.date
        prev_ts = context.bot_data["spam_stopper"].get(_id, None)

        if (prev_ts is not None and
            (ts - prev_ts).total_seconds() < 3 and
            _id != context.bot_data["master"]):
            msg = False

        context.bot_data["spam_stopper"][_id] = ts

        if msg:
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
    """Delete dice messages."""
    if update.message.dice.emoji in const.emojis:
        await asyncio.sleep(random.choice(const.CHOICES))
        await context.bot.delete_message(update.effective_chat.id, update.message.message_id)
        logger.info(f"delete_dice: msg_id={update.message.message_id}")


@pause
async def roll_dice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Roll a dice."""
    await context.bot.send_dice(
        chat_id=update.effective_message.chat_id,
        reply_to_message_id=update.message.message_id,
    )
    logger.info("roll_dice: success")


@pause
async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear AI context."""
    context.bot_data["chat_deque"] = deque(maxlen=100)
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Контекст очищен")


async def paused(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle bot pause state."""
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
