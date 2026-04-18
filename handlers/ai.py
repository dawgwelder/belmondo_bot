"""AI-powered handlers: process_ai_response, ai_horoscope, tarot, magic_prediction, clear_context."""

import datetime
import json
import random
from collections import deque

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
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


_AI_HOROSCOPE_PLACEHOLDER = (
    "🔮 Бельмондо разглядывает звёзды… _составляю гороскоп._"
)
_TAROT_PLACEHOLDER = (
    "🎴 Бельмондо тасует колоду и раскладывает карты… _слушаю шёпот арканов._"
)


async def _send_placeholder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    log_prefix: str,
):
    """Send a reply placeholder + typing action; returns the Message or None."""
    placeholder = None
    try:
        placeholder = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id if update.message else None,
            text=text,
            parse_mode="markdown",
        )
    except Exception:
        logger.exception("%s: failed to send placeholder", log_prefix)

    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action=ChatAction.TYPING
        )
    except Exception:
        logger.exception("%s: failed to send chat action", log_prefix)

    return placeholder


async def _fail_placeholder(
    context: ContextTypes.DEFAULT_TYPE,
    placeholder,
    chat_id: int,
    text: str,
    *,
    log_prefix: str,
) -> None:
    """Edit the placeholder with an error message, or send a new message as fallback."""
    if placeholder is not None:
        try:
            await placeholder.edit_text(text=text, parse_mode="markdown")
            return
        except Exception:
            logger.exception("%s: failed to edit error placeholder", log_prefix)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode="markdown",
    )


async def _finalize_placeholder(
    context: ContextTypes.DEFAULT_TYPE,
    placeholder,
    text: str,
    *,
    parse_mode: str = "markdown",
    log_prefix: str = "ai",
) -> None:
    """Edit the placeholder in place, or delete and send_long_message if too long."""
    from config import TELEGRAM_MAX_MESSAGE_LENGTH

    if placeholder is not None and len(text) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        try:
            await placeholder.edit_text(text=text, parse_mode=parse_mode)
            return
        except Exception:
            logger.exception("%s: failed to edit placeholder, falling back", log_prefix)

    if placeholder is not None:
        try:
            await placeholder.delete()
        except Exception:
            logger.exception("%s: failed to delete placeholder", log_prefix)

    await send_long_message(
        bot=context.bot,
        chat_id=placeholder.chat_id if placeholder is not None else None,
        text=text,
        parse_mode=parse_mode,
    )


@pause
async def ai_horoscope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send AI horoscope with a live placeholder message showing progress."""
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

    placeholder = await _send_placeholder(
        update, context, _AI_HOROSCOPE_PLACEHOLDER, log_prefix="ai_horoscope"
    )

    try:
        stream = await client.chat.completions.create(
            model="deepseek-chat", messages=messages, stream=True
        )
        text = await parse_stream(stream)
    except Exception:
        logger.exception("ai_horoscope: API error")
        await _fail_placeholder(
            context,
            placeholder,
            update.effective_chat.id,
            "Не удалось сгенерировать гороскоп. Попробуйте позже.",
            log_prefix="ai_horoscope",
        )
        return

    if not (text or "").strip():
        logger.warning("ai_horoscope: empty model response")
        await _fail_placeholder(
            context,
            placeholder,
            update.effective_chat.id,
            "Пустой ответ модели. Попробуйте ещё раз.",
            log_prefix="ai_horoscope",
        )
        return

    horoscope_history.append(text)
    await _finalize_placeholder(context, placeholder, text, log_prefix="ai_horoscope")
    logger.info(
        "ai_horoscope: user_message_len=%s horoscope_history_count=%s text_len=%s",
        len(prompt),
        len(horoscope_history),
        len(text),
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
    """Three-card tarot reading via DeepSeek, with a live status placeholder."""
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

    cards_header = "*Расклад:*\n" + "\n".join(
        f"• *{c['time'].capitalize()}* — {c['card']} ({c['orientation']})"
        for c in spread
    )
    placeholder = await _send_placeholder(
        update,
        context,
        f"{_TAROT_PLACEHOLDER}\n\n{cards_header}",
        log_prefix="tarot",
    )

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
        await _fail_placeholder(
            context,
            placeholder,
            update.effective_chat.id,
            "Не удалось сделать расклад. Попробуйте позже.",
            log_prefix="tarot",
        )
        return

    if not (text or "").strip():
        logger.warning("tarot: empty model response")
        await _fail_placeholder(
            context,
            placeholder,
            update.effective_chat.id,
            "Пустой ответ модели. Попробуйте ещё раз.",
            log_prefix="tarot",
        )
        return

    final_text = f"{cards_header}\n\n{text}"
    await _finalize_placeholder(context, placeholder, final_text, log_prefix="tarot")
    logger.info("tarot: delivered reading text_len=%s", len(text))


MAGIC_PREDICTION_CALLBACK = "mp:flip"
_DIVINE_LUCK_ODDS = 9999


def _roll_luck_level() -> int:
    """Roll a luck level in [0, 100] with a 1-in-9999 chance of divine 9999."""
    if random.randint(1, _DIVINE_LUCK_ODDS) == 1:
        return 9999
    return random.randint(0, 100)


def _build_magic_prediction_messages(luck_level: int) -> list[dict]:
    now = datetime.datetime.now(tz)
    weekday_ru = _WEEKDAYS_RU[now.weekday()]
    date_msk = now.strftime("%d.%m.%Y")
    user_content = (
        f"Сегодня по Москве: {weekday_ru}, {date_msk}.\n"
        f"Уровень удачи сегодня — {luck_level}.\n\n"
        "Шкала уровня удачи (`luck_level`):\n"
        "• 0 — ужасный день, всё сыпется из рук;\n"
        "• 50 — обычный серый день, ни плохо ни хорошо;\n"
        "• 100 — великолепный день, всё складывается само;\n"
        "• 9999 — божественная удача, выпадающая раз в 9999 лет, "
        "подчеркни её исключительность.\n\n"
        "Сгенерируй шуточное предсказание на этот день с учётом уровня удачи: "
        "ровно одно или два предложения — намекни, как может пройти день, "
        "чего опасаться или к чему стремиться. Тон и настроение ответа должны "
        "соответствовать уровню удачи. Без списков и без длинных абзацев; "
        "заверши короткой французской фразой с переводом в скобках."
    )
    return [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": user_content},
    ]


def _magic_prediction_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🪙 Подкинуть монетку", callback_data=MAGIC_PREDICTION_CALLBACK)]]
    )


@pause
async def magic_prediction(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Предложить подкинуть монетку, чтобы получить предсказание."""
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        reply_to_message_id=update.message.message_id,
        text="Подкинь монетку, и Бельмондо нашепчет тебе судьбу.",
        reply_markup=_magic_prediction_keyboard(),
    )


async def magic_prediction_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Callback for the 'подкинуть монетку' button: roll luck and fetch prediction."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()

    if not await ensure_master_in_chat_for_ai(update, context):
        return

    luck_level = _roll_luck_level()
    logger.info("magic_prediction: luck_level=%s chat=%s", luck_level, update.effective_chat.id)

    try:
        await query.edit_message_text(
            text=f"🪙 Монетка подброшена… уровень удачи: *{luck_level}*.\n_Бельмондо задумался…_",
            parse_mode="markdown",
        )
    except Exception:
        logger.exception("magic_prediction: failed to edit placeholder message")

    try:
        stream = await client.chat.completions.create(
            model="deepseek-chat",
            messages=_build_magic_prediction_messages(luck_level),
            stream=True,
            max_tokens=220,
        )
        text = await parse_stream(stream)
    except Exception:
        logger.exception("magic_prediction: API error")
        try:
            await query.edit_message_text(
                text="Не удалось сгенерировать предсказание. Попробуйте позже.",
                reply_markup=_magic_prediction_keyboard(),
                parse_mode="markdown",
            )
        except Exception:
            logger.exception("magic_prediction: failed to edit error message")
        return

    if not (text or "").strip():
        logger.warning("magic_prediction: empty model response")
        try:
            await query.edit_message_text(
                text="Пустой ответ модели. Попробуйте ещё раз.",
                reply_markup=_magic_prediction_keyboard(),
                parse_mode="markdown",
            )
        except Exception:
            logger.exception("magic_prediction: failed to edit empty-response message")
        return

    header = f"🪙 Уровень удачи: *{luck_level}*\n\n"
    final_text = header + text
    try:
        await query.edit_message_text(text=final_text, parse_mode="markdown")
    except Exception:
        logger.exception(
            "magic_prediction: failed to edit final message, falling back to a new send"
        )
        await send_long_message(
            bot=context.bot,
            chat_id=update.effective_chat.id,
            text=final_text,
            parse_mode="markdown",
        )
    logger.info(
        "magic_prediction: delivered prediction luck_level=%s len=%s",
        luck_level,
        len(text),
    )


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
