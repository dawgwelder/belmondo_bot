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
from state import ensure_chat_state
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


def _ensure_system_prompt(chat_deque: deque, prompt: str) -> None:
    """Keep exactly one system prompt at the head of the deque."""
    if chat_deque and chat_deque[0].get("role") == "system":
        if chat_deque[0].get("content") != prompt:
            chat_deque[0] = {"role": "system", "content": prompt}
        return
    chat_deque.appendleft({"role": "system", "content": prompt})


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

    chat_data = ensure_chat_state(context)
    lock = chat_data["ai_lock"]

    if lock.locked():
        logger.info(
            "process_ai_response: concurrent request ignored chat_id=%s",
            update.effective_chat.id,
        )
        return

    async with lock:
        content = update.message.text
        model_type = (
            "deepseek-reasoner"
            if content.lower().startswith("подумай")
            else "deepseek-chat"
        )

        chat_deque = chat_data["chat_deque"]
        _ensure_system_prompt(chat_deque, const.professional_prompt)
        chat_deque.append({"role": "user", "content": content})

        try:
            if "гороскоп" not in content:
                stream = await client.chat.completions.create(
                    model=model_type,
                    messages=list(chat_deque),
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

            chat_deque.append({"role": "assistant", "content": text})

            await send_long_message(
                bot=context.bot,
                chat_id=update.effective_chat.id,
                text=text,
                parse_mode="markdown",
                reply_to_message_id=update.message.message_id,
            )
            logger.info(f"chatGPT: generated text sent text:{text}")

        except Exception:
            logger.exception("process_ai_response: AI generation failed")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                reply_to_message_id=update.message.message_id,
                text="Извините, произошла ошибка при генерации ответа.",
                parse_mode="markdown",
            )


@pause
async def ai_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send AI horoscope."""
    if update.effective_user.id in const.excluded_uids:
        return
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    chat_data = ensure_chat_state(context)
    horoscope_history = chat_data["horoscope_history"]
    history = list(horoscope_history) if horoscope_history else None
    prompt = build_ai_horoscope_user_message(history)
    messages = [
        {"role": "system", "content": const.professional_prompt_ai_horoscope},
        {"role": "user", "content": prompt},
    ]
    if horoscope_history:
        previous_messages = [
            {"role": "assistant", "content": message}
            for message in horoscope_history
        ]
        messages = previous_messages + messages

    try:
        stream = await client.chat.completions.create(
            model="deepseek-chat", messages=messages, stream=True
        )
        text = await parse_stream(stream)
    except Exception:
        logger.exception("ai_horoscope: API error")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось сгенерировать гороскоп. Попробуйте позже.",
            parse_mode="markdown",
        )
        return

    if not (text or "").strip():
        logger.warning("ai_horoscope: empty model response")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Пустой ответ модели. Попробуйте ещё раз.",
            parse_mode="markdown",
        )
        return

    horoscope_history.append(text)
    await send_long_message(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="markdown",
    )
    logger.info(
        "ai_horoscope: user_message_len=%s horoscope_history_count=%s",
        len(prompt),
        len(horoscope_history),
    )


def _build_tarot_spread(deck: list[dict]) -> list[dict]:
    sample = random.sample(deck, k=3)
    result = []
    for card, time in zip(sample, ["прощлое", "настоящее", "будущее"]):
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
    return result


@pause
async def tarot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Three-card tarot reading via DeepSeek."""
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    deck = context.bot_data.get("tarot_deck")
    if not deck:
        logger.error("tarot: tarot_deck not initialised in bot_data")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="Колода таро не загружена. Попробуйте позже.",
            parse_mode="markdown",
        )
        return

    spread = _build_tarot_spread(deck)
    tarot_prompt = generate_tarot_prompt(spread)

    request = [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": tarot_prompt},
    ]

    try:
        stream = await client.chat.completions.create(
            model="deepseek-reasoner", messages=request, stream=True
        )
        text = await parse_stream(stream)
    except Exception:
        logger.exception("tarot: API error")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text="Не удалось сделать расклад. Попробуйте позже.",
            parse_mode="markdown",
        )
        return

    if not (text or "").strip():
        logger.warning("tarot: empty model response")
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
    except Exception:
        logger.exception("magic_prediction: API error")
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
    chat_data = ensure_chat_state(context)
    chat_data["chat_deque"] = deque(maxlen=100)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Контекст очищен"
    )


# kept for a JSON loader used at application startup
def load_tarot_deck(path: str = "tarot_cards.json") -> list[dict]:
    """Load and return the tarot deck once at process start."""
    with open(path) as f:
        return json.load(f)["cards"]
