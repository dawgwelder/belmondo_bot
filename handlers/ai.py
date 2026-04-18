"""AI-powered handlers: process_ai_response, ai_horoscope, tarot, magic_prediction, clear_context."""

import datetime
import json
import random
from collections import deque

from telegram import Update
from telegram.ext import ContextTypes

import const
from config import client, logger, tz
from guards import ensure_master_in_chat_for_ai, pause
from horoscope import build_ai_horoscope_user_message, generate_tarot_prompt
from telegram_utils import parse_stream, send_long_message


_WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)


async def process_ai_response(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process AI chat responses to bot replies."""
    if not (
        update.message is not None
        and update.message.reply_to_message is not None
        and update.message.reply_to_message.from_user.id
        == context.bot_data["self_id"]
        and update.message.from_user.id not in const.excluded_uids
    ):
        return

    if not await ensure_master_in_chat_for_ai(update, context):
        return

    content = update.message.text
    model_type = (
        "deepseek-reasoner" if content.lower().startswith("подумай") else "deepseek-chat"
    )

    context.bot_data["chat_deque"].append(
        {"role": "system", "content": const.professional_prompt}
    )
    context.bot_data["chat_deque"].append({"role": "user", "content": content})

    try:
        text = ""
        if "гороскоп" not in content:
            stream = await client.chat.completions.create(
                model=model_type,
                messages=list(context.bot_data["chat_deque"]),
                stream=True,
            )
            text = await parse_stream(stream)
        else:
            stream = await client.chat.completions.create(
                model=model_type,
                messages=[
                    {"role": "system", "content": const.professional_prompt},
                    {"role": "user", "content": content},
                ],
                stream=True,
            )
            text = await parse_stream(stream)

        context.bot_data["chat_deque"].append(
            {"role": "assistant", "content": text}
        )

        await send_long_message(
            bot=context.bot,
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode="markdown",
            reply_to_message_id=update.message.message_id,
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
    if update.effective_user.id in const.excluded_uids:
        return
    history = (
        list(context.bot_data["horoscope_history"])
        if context.bot_data["horoscope_history"]
        else None
    )
    prompt = build_ai_horoscope_user_message(history)
    messages = [
        {"role": "system", "content": const.professional_prompt_ai_horoscope},
        {"role": "user", "content": prompt},
    ]
    if context.bot_data["horoscope_history"]:
        previous_messages = [
            {"role": "assistant", "content": message}
            for message in context.bot_data["horoscope_history"]
        ]
        messages = previous_messages + messages

    text = ""
    try:
        stream = await client.chat.completions.create(
            model="deepseek-chat", messages=messages, stream=True
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
            parse_mode="markdown",
        )
    logger.info(
        "ai_horoscope: user_message_len=%s horoscope_history_count=%s",
        len(prompt),
        len(context.bot_data["horoscope_history"]),
    )


@pause
async def tarot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Three-card tarot reading via DeepSeek."""
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    result = []
    with open("tarot_cards.json") as f:
        deck = random.sample(json.load(f)["cards"], k=3)
    for card, time in zip(deck, ["прощлое", "настоящее", "будущее"]):
        reversed_flag = random.choice([True, False])
        result.append(
            {
                "card": card["name"],
                "orientation": "перевернутая" if reversed_flag else "прямая",
                "meaning": card["reversed_meaning"]
                if reversed_flag
                else card["upright_meaning"],
                "time": time,
            }
        )
    tarot_prompt = generate_tarot_prompt(result)

    request = [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": tarot_prompt},
    ]

    stream = await client.chat.completions.create(
        model="deepseek-reasoner", messages=request, stream=True
    )
    text = await parse_stream(stream)

    await send_long_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="markdown",
        reply_to_message_id=update.message.message_id,
    )


@pause
async def magic_prediction(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
async def clear_context(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear AI context."""
    context.bot_data["chat_deque"] = deque(maxlen=100)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Контекст очищен"
    )
