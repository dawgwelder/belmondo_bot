"""Spy Clicker Telegram callback investigation."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.models import ClaimStatus, FindMoleGameStatus, InterceptStatus
from spy_game.settings import AGENT_TYPES, ITEM_TYPES
from .context import _service
from .formatting import _display_name, _public_label
from .transport import _remove_event_keyboard, _send_rich


async def handle_mole(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    suspect_id = category.removeprefix("mole_")
    try:
        result = await service.accuse_find_mole_event(
            event_id=value,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
            suspect_id=suspect_id,
            idempotency_key=query.id,
        )
    except Exception:
        logger.exception("spy_game: mole accusation failed event_id=%s", value)
        await query.answer(
            "Центр не принял версию. Попробуйте ещё раз.",
            show_alert=True,
        )
        return
    if result.status is FindMoleGameStatus.WON:
        item = ITEM_TYPES[result.item_reward.reward_id]
        agent = AGENT_TYPES[result.agent_reward.agent_type]
        reward_text = (
            f"{item.emoji} {item.display_name} ×{result.item_reward.amount} и "
            f"{agent.emoji} {agent.display_name} ×{result.agent_reward.amount}"
        )
        await query.answer(f"Крот раскрыт. Награда: {reward_text}", show_alert=True)
        await _remove_event_keyboard(context, chat.id, result.message_id)
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "✅ КРОТ РАСКРЫТ"},
                {
                    "type": "paragraph",
                    "text": (
                        f"{_public_label(user)} завершил расследование и получил "
                        f"{reward_text}."
                    ),
                },
            ],
            fallback_text=(
                f"✅ {_public_label(user)} раскрыл крота и получил {reward_text}."
            ),
        )
    elif result.status is FindMoleGameStatus.FAILED:
        await query.answer(
            "Версия оказалась неверной. Ваша попытка завершена; остальные "
            "агенты могут продолжить.",
            show_alert=True,
        )
    elif result.status is FindMoleGameStatus.ALREADY_PLAYED:
        await query.answer("Вы уже выдвинули финальную версию.", show_alert=True)
    elif result.status is FindMoleGameStatus.EXPIRED:
        await query.answer("Время расследования закончилось.", show_alert=True)
    elif result.status is FindMoleGameStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент уже раскрыл крота.", show_alert=True)
    else:
        await query.answer("Это дело сейчас недоступно.", show_alert=True)
    return


async def handle_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        result = await service.search_dead_drop(
            event_id=value,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: dead drop failed event_id=%s", value)
        await query.answer(
            "Не удалось открыть тайник. Попробуйте ещё раз.", show_alert=True
        )
        return
    if result.status is ClaimStatus.WON:
        reward = result.reward
        if reward.reward_type == "item":
            item = ITEM_TYPES[reward.reward_id]
            reward_text = f"{item.emoji} {item.display_name} ×{reward.amount}"
        elif reward.reward_type == "agent":
            agent = AGENT_TYPES[reward.reward_id]
            reward_text = f"{agent.emoji} {agent.display_name} ×{reward.amount}"
        else:
            reward_text = "тайник оказался пуст"
        await query.answer(f"Результат: {reward_text}", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("spy_game: dead drop could not remove keyboard")
        logger.info(
            "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
            "reward_type=%s reward_id=%s reward_amount=%s",
            result.event_id,
            chat.id,
            user.id,
            reward.reward_type,
            reward.reward_id,
            reward.amount,
        )
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "✅ ТАЙНИК ВСКРЫТ"},
                {
                    "type": "paragraph",
                    "text": f"{_public_label(user)} нашёл: {reward_text}.",
                },
            ],
            fallback_text=f"✅ {_public_label(user)} нашёл: {reward_text}.",
        )
    elif result.status is ClaimStatus.EXPIRED:
        await query.answer("Тайник уже изъят Центром.", show_alert=True)
    elif result.status is ClaimStatus.ALREADY_RESOLVED:
        await query.answer("Тайник уже обыскали.", show_alert=True)
    else:
        await query.answer("Тайник недоступен.", show_alert=True)
    return


async def handle_intercept(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    choice_id = category.removeprefix("intercept_")
    try:
        result = await service.answer_intercept(
            event_id=value,
            choice_id=choice_id,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: intercept failed event_id=%s", value)
        await query.answer(
            "Не удалось проверить расшифровку. Попробуйте ещё раз.",
            show_alert=True,
        )
        return
    if result.status in {InterceptStatus.CORRECT, InterceptStatus.INCORRECT}:
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("spy_game: intercept could not remove keyboard")
        if result.status is InterceptStatus.CORRECT:
            item = ITEM_TYPES[result.reward.reward_id]
            answer = f"Верно: {item.display_name} зачислен."
            title = "✅ ШИФР РАСКРЫТ"
            body = (
                f"{_public_label(user)} перехватил канал и получил "
                f"{item.emoji} {item.display_name} ×{result.reward.amount}."
            )
        else:
            answer = "Неверная расшифровка. Канал сменил частоту."
            title = "❌ КАНАЛ ПОТЕРЯН"
            body = f"Ответ {_public_label(user)} оказался ложным следом."
        await query.answer(answer, show_alert=True)
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": title},
                {"type": "paragraph", "text": body},
            ],
            fallback_text=f"{title}\n{body}",
        )
    elif result.status is InterceptStatus.EXPIRED:
        await query.answer("Канал уже замолчал.", show_alert=True)
    elif result.status is InterceptStatus.ALREADY_RESOLVED:
        await query.answer("Кто-то уже отправил ответ.", show_alert=True)
    else:
        await query.answer("Этот вариант ответа недоступен.", show_alert=True)
    return
