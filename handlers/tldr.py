"""Handler for /tldr — LLM narrative summary of recent chat messages."""

from telegram import Update
from telegram.ext import ContextTypes

from config import client, logger
from guards import ensure_master_in_chat_for_ai, pause
from handlers.ai import _fail_placeholder, _send_placeholder
from state import ensure_chat_state
from telegram_utils import parse_stream, send_long_message
from tldr.history import fetch_chat_text_messages
from tldr.summarize import (
    MAX_LIMIT,
    build_tldr_messages,
    format_history_for_prompt,
    parse_limit,
)

_TLDR_PLACEHOLDER = "📜 Бельмондо листает историю чата… _составляю сводку._"
_TLDR_MODEL = "deepseek-v4-flash"


@pause
async def tldr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Summarize the last N chat messages in Belmondo voice."""
    if update.message is None or update.effective_chat is None:
        return
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    limit, error, clamped = parse_limit(context.args)
    if error is not None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            reply_to_message_id=update.message.message_id,
            text=error,
        )
        return

    chat_data = ensure_chat_state(context)
    lock = chat_data["ai_lock"]
    if lock.locked():
        logger.info(
            "tldr: concurrent request ignored chat_id=%s",
            update.effective_chat.id,
        )
        return

    async with lock:
        placeholder = await _send_placeholder(
            update, context, _TLDR_PLACEHOLDER, log_prefix="tldr"
        )
        chat_id = update.effective_chat.id
        command_message_id = update.message.message_id

        try:
            history = await fetch_chat_text_messages(
                chat_id,
                limit,
                exclude_message_id=command_message_id,
            )
        except Exception:
            logger.exception("tldr: Telethon history fetch failed chat_id=%s", chat_id)
            await _fail_placeholder(
                context,
                placeholder,
                chat_id,
                "Не удалось прочитать историю чата. Проверьте сессию Telethon.",
                log_prefix="tldr",
            )
            return

        if not history:
            await _fail_placeholder(
                context,
                placeholder,
                chat_id,
                "В выбранном окне нет текстовых сообщений для суммаризации.",
                log_prefix="tldr",
            )
            return

        messages = build_tldr_messages(format_history_for_prompt(history))

        try:
            stream = await client.chat.completions.create(
                model=_TLDR_MODEL,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}},
                messages=messages,
                stream=True,
            )
            text = await parse_stream(stream)
        except Exception:
            logger.exception("tldr: LLM API error")
            await _fail_placeholder(
                context,
                placeholder,
                chat_id,
                "Не удалось сгенерировать сводку. Попробуйте позже.",
                log_prefix="tldr",
            )
            return

        if not (text or "").strip():
            logger.warning("tldr: empty model response")
            await _fail_placeholder(
                context,
                placeholder,
                chat_id,
                "Пустой ответ модели. Попробуйте ещё раз.",
                log_prefix="tldr",
            )
            return

        header = ""
        if clamped:
            header = (
                f"_Запрошено больше {MAX_LIMIT} — суммаризирую последние "
                f"{MAX_LIMIT} сообщений._\n\n"
            )
        final_text = header + text

        try:
            if placeholder is not None:
                await placeholder.delete()
        except Exception:
            logger.exception("tldr: failed to delete placeholder")

        await send_long_message(
            bot=context.bot,
            chat_id=chat_id,
            text=final_text,
            parse_mode="markdown",
            reply_to_message_id=command_message_id,
        )
        logger.info(
            "tldr: delivered summary chat_id=%s limit=%s history=%s text_len=%s clamped=%s",
            chat_id,
            limit,
            len(history),
            len(text),
            clamped,
        )
