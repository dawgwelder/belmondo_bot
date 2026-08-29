"""AI-powered handlers: process_ai_response, ai_horoscope, tarot, magic_prediction, clear_context."""

import asyncio
import datetime
import json
import random
import re
from collections import deque
from collections.abc import Sequence
from contextlib import asynccontextmanager

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

import const
from config import client, logger, tz
from guards import ensure_master_in_chat_for_ai, pause
from horoscope import build_ai_horoscope_user_message, generate_tarot_prompt
from state import ensure_chat_state
from telegram_utils import parse_stream, send_long_message, send_rich_message


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

        chat_deque = chat_data["chat_deque"]
        _ensure_system_prompt(chat_deque, const.professional_prompt)
        chat_deque.append({"role": "user", "content": content})

        try:
            stream = await client.chat.completions.create(
                model="deepseek-v4-flash",
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
                messages=list(chat_deque),
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


_PLACEHOLDER_ANIMATION_INTERVAL = 2.8

_AI_HOROSCOPE_PLACEHOLDER = (
    "🔮 Бельмондо разглядывает звёзды… _составляю гороскоп._",
    "🔮 Бельмондо вышел на охоту за Ретроградным Меркурием… _составляю гороскоп._",
    "🔮 Сверяю эфемериды с сигаретой за ухом… _ещё минуту._",
    "🔮 Допрашиваю Марс по поводу вашей удачи… _знаки нервничают._",
    "🔮 Черчу карту неба на салфетке из бистро… _почти готово._",
    "🔮 Перекладываю зодиакальные досье… _составляю гороскоп._",
)
_TAROT_PLACEHOLDER = (
    "🎴 Бельмондо тасует колоду и раскладывает карты… _слушаю шёпот арканов._",
    "🎴 Карты спорят между собой… _навожу порядок в раскладе._",
    "🎴 Переворачиваю арканы лицом вниз… _считаю удачу._",
    "🎴 Сдуваю пыль с колоды… _расклад почти готов._",
)
_ZODIAC_RU = [
    "Овен",
    "Телец",
    "Близнецы",
    "Рак",
    "Лев",
    "Дева",
    "Весы",
    "Скорпион",
    "Стрелец",
    "Козерог",
    "Водолей",
    "Рыбы",
]


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


async def _cycle_placeholder_text(
    context: ContextTypes.DEFAULT_TYPE,
    placeholder,
    frames: Sequence[str],
    stop: asyncio.Event,
    *,
    interval: float = _PLACEHOLDER_ANIMATION_INTERVAL,
    log_prefix: str = "ai",
    chat_id: int | None = None,
) -> None:
    """Rotate placeholder text until stop is set. Frame 0 is assumed already shown."""
    if placeholder is None or len(frames) < 2:
        await stop.wait()
        return

    idx = 1
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass

        text = frames[idx % len(frames)]
        idx += 1
        try:
            await placeholder.edit_text(text=text, parse_mode="markdown")
        except Exception:
            logger.debug("%s: placeholder animation edit skipped", log_prefix)

        if chat_id is not None:
            try:
                await context.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.TYPING
                )
            except Exception:
                logger.debug("%s: placeholder typing refresh skipped", log_prefix)


@asynccontextmanager
async def _animated_placeholder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    frames: Sequence[str],
    *,
    log_prefix: str,
    interval: float = _PLACEHOLDER_ANIMATION_INTERVAL,
):
    """Send a placeholder and rotate its text while the caller awaits work."""
    frame_list = [f for f in frames if f]
    if not frame_list:
        yield None
        return

    start = random.randrange(len(frame_list))
    ordered = frame_list[start:] + frame_list[:start]
    placeholder = await _send_placeholder(
        update, context, ordered[0], log_prefix=log_prefix
    )
    stop = asyncio.Event()
    task = asyncio.create_task(
        _cycle_placeholder_text(
            context,
            placeholder,
            ordered,
            stop,
            interval=interval,
            log_prefix=log_prefix,
            chat_id=update.effective_chat.id if update.effective_chat else None,
        )
    )
    try:
        yield placeholder
    finally:
        stop.set()
        try:
            await task
        except Exception:
            logger.exception("%s: placeholder animation task failed", log_prefix)


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
    """Edit the placeholder in place, or delete and send a single message."""
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

    await context.bot.send_message(
        chat_id=placeholder.chat_id if placeholder is not None else None,
        text=text,
        parse_mode=parse_mode,
    )


_TAROT_SECTION_TITLES = (
    "Прошлое",
    "Настоящее",
    "Будущее",
    "Итог",
    "Совет",
)


def _tarot_section_heading_re() -> re.Pattern[str]:
    titles = "|".join(_TAROT_SECTION_TITLES)
    return re.compile(
        rf"^(?:[-•\s]*)[*_~`\"]*(?:{titles})[*_~`\"]*[.:]?\s*$",
        flags=re.IGNORECASE,
    )


def _normalize_tarot_section_title(line: str) -> str | None:
    cleaned = re.sub(r"^[-•\s]+", "", (line or "").strip())
    cleaned = re.sub(r"^[*_~`\"]+|[*_~`\"]+$", "", cleaned)
    cleaned = cleaned.rstrip(".:").strip()
    for title in _TAROT_SECTION_TITLES:
        if cleaned.lower() == title.lower():
            return title
    return None


def _looks_like_french_footer(text: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ][^()\n]*\([^)\n]{3,}\)", (text or "").strip()))


def _parse_tarot_reading(text: str) -> tuple[str, dict[str, str], str]:
    """Split tarot LLM text into intro, named sections, and footer."""
    source = (text or "").strip()
    if not source:
        return "", {}, ""

    heading_re = _tarot_section_heading_re()
    lines = source.splitlines()
    starts: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        title = _normalize_tarot_section_title(line)
        if title is not None and heading_re.match(line.strip()):
            starts.append((idx, title))

    if not starts:
        # No labeled sections — keep body for fallback details; peel French footer.
        if "\n\n" in source:
            head, tail = source.rsplit("\n\n", 1)
            if _looks_like_french_footer(tail):
                return "", {}, tail.strip()
        return "", {}, ""

    intro = "\n".join(lines[: starts[0][0]]).strip()
    sections: dict[str, str] = {}
    for i, (start_idx, title) in enumerate(starts):
        end_idx = starts[i + 1][0] if i + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start_idx + 1 : end_idx]).strip()
        sections[title] = body

    footer = ""
    last_title = starts[-1][1]
    last_body = sections.get(last_title, "")
    if "\n\n" in last_body:
        head, tail = last_body.rsplit("\n\n", 1)
        if _looks_like_french_footer(tail):
            sections[last_title] = head.strip()
            footer = tail.strip()
    return intro, sections, footer


def _tarot_spread_header(spread: list[dict]) -> str:
    lines = ["Расклад:"]
    for card in spread:
        lines.append(
            f"• {card['time'].capitalize()} — {card['card']} ({card['orientation']})"
        )
    return "\n".join(lines)


def build_tarot_blocks(spread: list[dict], text: str) -> list[dict]:
    """Build InputRichMessage blocks for a tarot reading."""
    blocks: list[dict] = [
        {"type": "paragraph", "text": _tarot_spread_header(spread)},
    ]
    intro, sections, footer = _parse_tarot_reading(text)
    if intro:
        blocks.append({"type": "paragraph", "text": intro})

    has_section = False
    for title in _TAROT_SECTION_TITLES:
        body = (sections.get(title) or "").strip()
        if not body:
            continue
        has_section = True
        blocks.append(
            {
                "type": "details",
                "summary": title,
                "blocks": [{"type": "paragraph", "text": body}],
            }
        )

    if not has_section:
        content = (text or "").strip()
        if footer and content.endswith(footer):
            content = content[: -len(footer)].strip()
        if content:
            blocks.append(
                {
                    "type": "details",
                    "summary": "Гадание",
                    "blocks": [{"type": "paragraph", "text": content}],
                }
            )

    if footer:
        blocks.append({"type": "footer", "text": footer})
    return blocks


async def _send_tarot_structured(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    spread: list[dict],
    text: str,
) -> None:
    """Send tarot reading as a single Rich Message via httpx."""
    blocks = build_tarot_blocks(spread, text)
    if not blocks:
        await context.bot.send_message(
            chat_id=chat_id, text="Пустой расклад — нечего отправлять."
        )
        return
    try:
        await send_rich_message(context.bot.token, chat_id, blocks)
    except Exception:
        logger.exception("tarot: sendRichMessage failed")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Не удалось отправить расклад. Попробуйте позже.",
        )


def _split_horoscope_sections(text: str) -> tuple[str, list[str]]:
    """Split AI horoscope into header and zodiac sections."""
    source = (text or "").strip()
    if not source:
        return "", []

    if "\n" in source:
        header, rest = source.split("\n", 1)
        rest = rest.strip()
    else:
        return source, []

    starts: list[int] = []
    offset = 0
    zodiac_pattern = re.compile(
        rf"^(?:[-•\s]*)[*_~`\"]*(?:{'|'.join(_ZODIAC_RU)})[*_~`\"]*[.:]?(?:\s|$)",
        flags=re.IGNORECASE,
    )

    for line in rest.splitlines(keepends=True):
        probe = line.strip()
        if zodiac_pattern.match(probe):
            starts.append(offset)
        offset += len(line)

    if not starts:
        return header.strip(), [rest] if rest else []

    sections: list[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(rest)
        section = rest[start:end].strip()
        if section:
            sections.append(section)
    return header.strip(), sections


def _split_zodiac_section(section: str) -> tuple[str, str]:
    """Split a zodiac section into sign name and full forecast body.

    Only the sign name is returned as title; the entire forecast — including
    the first sentence — goes into the body.
    """
    source = (section or "").strip()
    if not source:
        return "", ""

    first_line, _, rest = source.partition("\n")
    match = re.match(
        rf"^(?:[-•\s]*)[*_~`\"]*(?P<name>{'|'.join(_ZODIAC_RU)})[*_~`\"]*[.:]?\s*"
        rf"(?P<inline>.*)$",
        first_line.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return "", source

    name = _strip_markdown_wrappers(match.group("name").strip())
    parts: list[str] = []
    inline = (match.group("inline") or "").strip()
    if inline:
        parts.append(inline)
    if rest.strip():
        parts.append(rest.strip())
    return name, "\n".join(parts).strip()


def _strip_markdown_wrappers(text: str) -> str:
    """Remove Markdown markers commonly added around zodiac titles."""
    source = text.strip()
    zodiac_name = "|".join(_ZODIAC_RU)
    source = re.sub(
        rf"^[*_~`]+(\s*(?:{zodiac_name})\s*)[*_~`]+",
        r"\1",
        source,
        flags=re.IGNORECASE,
    )
    return re.sub(r"^[*_~`]+|[*_~`]+$", "", source)


def _extract_horoscope_footer(sections: list[str]) -> str:
    """Pull a trailing French phrase+translation from the last zodiac section."""
    if not sections:
        return ""
    last_section = sections[-1]
    if "\n\n" not in last_section:
        return ""
    head, tail = last_section.rsplit("\n\n", 1)
    tail_clean = tail.strip()
    has_translation_parentheses = bool(
        re.search(r"[A-Za-zÀ-ÿ][^()\n]*\([^)\n]{3,}\)", tail_clean)
    )
    if not (tail_clean and has_translation_parentheses):
        return ""
    sections[-1] = head.strip()
    return tail_clean


def build_ai_horoscope_blocks(text: str) -> list[dict]:
    """Build InputRichMessage blocks for an AI horoscope.

    Skeleton: header paragraph, one collapsed details per sign (summary =
    sign name only), optional footer. Full forecast text lives inside details.
    """
    header, sections = _split_horoscope_sections(text)
    if not sections:
        content = (text or "").strip()
        if not content:
            return []
        return [{"type": "expandable_blockquote", "text": content}]

    footer = _extract_horoscope_footer(sections)
    blocks: list[dict] = []
    if header.strip():
        blocks.append({"type": "paragraph", "text": header.strip()})

    for section in sections:
        title_text, body = _split_zodiac_section(section)
        if not title_text:
            if body:
                blocks.append({"type": "expandable_blockquote", "text": body})
            continue
        if not body:
            blocks.append({"type": "paragraph", "text": title_text})
            continue
        blocks.append(
            {
                "type": "details",
                "summary": title_text,
                "blocks": [{"type": "paragraph", "text": body}],
            }
        )

    if footer:
        blocks.append({"type": "footer", "text": footer})
    return blocks


async def _send_ai_horoscope_structured(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
) -> None:
    """Send AI horoscope as a single Rich Message via httpx."""
    blocks = build_ai_horoscope_blocks(text)
    if not blocks:
        await context.bot.send_message(
            chat_id=chat_id, text="Пустой гороскоп — нечего отправлять."
        )
        return
    try:
        await send_rich_message(context.bot.token, chat_id, blocks)
    except Exception:
        logger.exception("ai_horoscope: sendRichMessage failed")
        await context.bot.send_message(
            chat_id=chat_id,
            text="Не удалось отправить гороскоп. Попробуйте позже.",
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
    prompt = await build_ai_horoscope_user_message(history)
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

    placeholder = None
    try:
        async with _animated_placeholder(
            update,
            context,
            _AI_HOROSCOPE_PLACEHOLDER,
            log_prefix="ai_horoscope",
        ) as placeholder:
            stream = await client.chat.completions.create(
                model="deepseek-v4-flash",
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
                messages=messages,
                stream=True,
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
    try:
        if placeholder is not None:
            await placeholder.delete()
    except Exception:
        logger.exception("ai_horoscope: failed to delete placeholder")

    await _send_ai_horoscope_structured(context, update.effective_chat.id, text)
    logger.info(
        "ai_horoscope: user_message_len=%s horoscope_history_count=%s text_len=%s",
        len(prompt),
        len(horoscope_history),
        len(text),
    )


def _build_tarot_spread(deck: list[dict]) -> list[dict]:
    sample = random.sample(deck, k=3)
    result = []
    for card, time in zip(sample, ["прошлое", "настоящее", "будущее"]):
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
    tarot_frames = tuple(f"{line}\n\n{cards_header}" for line in _TAROT_PLACEHOLDER)

    request = [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": tarot_prompt},
    ]

    placeholder = None
    try:
        async with _animated_placeholder(
            update, context, tarot_frames, log_prefix="tarot"
        ) as placeholder:
            stream = await client.chat.completions.create(
                model="deepseek-v4-flash",
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
                messages=request,
                stream=True,
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

    try:
        if placeholder is not None:
            await placeholder.delete()
    except Exception:
        logger.exception("tarot: failed to delete placeholder")

    await _send_tarot_structured(
        context, update.effective_chat.id, spread, text
    )
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
            model="deepseek-v4-flash",
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}},
            messages=_build_magic_prediction_messages(luck_level),
            stream=True,
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
