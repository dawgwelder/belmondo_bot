"""Spy Clicker Telegram callback operations."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.telegram_messages import compact_event_query
from spy_game.models import ChaseStatus, CooperativeStatus, DeathOperationStatus
from spy_game.settings import AGENT_TYPES
from .context import _service
from .formatting import _display_name, _public_label
from .transport import _send_rich


async def handle_cooperate(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        result = await service.contribute_cooperative(
            event_id=value,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: cooperative operation failed event_id=%s", value)
        await query.answer(
            "Центр не принял вклад. Попробуйте ещё раз.", show_alert=True
        )
        return
    if result.status is CooperativeStatus.CONTRIBUTED:
        await query.answer(
            f"Вклад принят: {result.contributions}/"
            f"{result.required_contributions} участников.",
            show_alert=True,
        )
    elif result.status is CooperativeStatus.COMPLETED:
        await compact_event_query(query, "✅ Совместная операция завершена.")
        agent = AGENT_TYPES[result.reward.agent_type]
        await query.answer(
            f"Цель достигнута. Каждый получает {agent.display_name} "
            f"×{result.reward.amount}.",
            show_alert=True,
        )
        body = (
            f"{result.contributions} участников замкнули сеть наблюдения. "
            f"Каждому начислено: {agent.emoji} {agent.display_name} "
            f"×{result.reward.amount}."
        )
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "✅ ОПЕРАЦИЯ ЗАВЕРШЕНА"},
                {"type": "paragraph", "text": body},
            ],
            fallback_text=f"✅ Операция завершена. {body}",
        )
    elif result.status is CooperativeStatus.ALREADY_CONTRIBUTED:
        await query.answer(
            f"Ваш вклад уже учтён: {result.contributions}/"
            f"{result.required_contributions}.",
            show_alert=True,
        )
    elif result.status is CooperativeStatus.ALREADY_RESOLVED:
        await query.answer("Операция уже укомплектована.", show_alert=True)
    elif result.status is CooperativeStatus.EXPIRED:
        await query.answer("Окно совместной операции закрыто.", show_alert=True)
        await compact_event_query(query, "⌛ Событие закрыто: время истекло.")
    else:
        await query.answer("Операция недоступна.", show_alert=True)
    return


async def handle_chase(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        result = await service.advance_chase(
            event_id=value,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: chase failed event_id=%s", value)
        await query.answer("Погоня сорвалась. Попробуйте ещё раз.", show_alert=True)
        return
    if result.status is ChaseStatus.STARTED:
        await query.answer(
            "Преследование началось. Теперь цель нужно перехватить.",
            show_alert=True,
        )
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Перехватить цель",
                                callback_data=f"spy:chase:{value}",
                            )
                        ]
                    ]
                )
            )
        except Exception:
            logger.warning("spy_game: chase could not switch to stage two")
    elif result.status is ChaseStatus.COMPLETED:
        await compact_event_query(query, "✅ Погоня завершена.")
        await query.answer("Цель перехвачена. Награды начислены.", show_alert=True)
        starter_agent = AGENT_TYPES[result.starter_reward.agent_type]
        interceptor_agent = AGENT_TYPES[result.interceptor_reward.agent_type]
        body = (
            f"Первый этап: {result.starter_name} получает "
            f"{starter_agent.emoji} {starter_agent.display_name} "
            f"×{result.starter_reward.amount}.\n"
            f"Перехват: {result.interceptor_name} получает "
            f"{interceptor_agent.emoji} {interceptor_agent.display_name} "
            f"×{result.interceptor_reward.amount}."
        )
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "✅ ЦЕЛЬ ПЕРЕХВАЧЕНА"},
                {"type": "paragraph", "text": body},
            ],
            fallback_text=f"✅ Цель перехвачена.\n{body}",
        )
    elif result.status is ChaseStatus.EXPIRED:
        await query.answer("Цель ушла от преследования.", show_alert=True)
        await compact_event_query(query, "⌛ Событие закрыто: время истекло.")
    elif result.status is ChaseStatus.ALREADY_RESOLVED:
        await query.answer("Погоня уже завершена.", show_alert=True)
    else:
        await query.answer("Погоня недоступна.", show_alert=True)
    return


async def handle_death(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    try:
        result = await service.run_death_operation(
            event_id=value,
            action="death",
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: death operation failed event_id=%s", value)
        await query.answer(
            "Центр потерял связь. Состав не изменён, попробуйте ещё раз.",
            show_alert=True,
        )
        return
    if result.status is DeathOperationStatus.CONFIRMATION_REQUIRED:
        total = sum(holding.amount for holding in result.staked)
        seconds = max(
            1,
            math.ceil(
                (
                    result.confirmation_expires_at - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )
        await query.answer(
            f"На кону все ваши агенты: {total}. Шанс успеха "
            f"{service.settings.death_operation_success_percent}%. "
            f"Нажмите ещё раз в течение {seconds} сек., чтобы подтвердить.",
            show_alert=True,
        )
        return
    if result.status in {
        DeathOperationStatus.WON,
        DeathOperationStatus.LOST,
    }:
        await compact_event_query(query, "💀 Смертельная операция завершена.")
        total_staked = sum(holding.amount for holding in result.staked)
        if result.status is DeathOperationStatus.WON:
            bonus = AGENT_TYPES[result.rewards[-1].agent_type]
            title = "✅ НЕВОЗМОЖНОЕ ВЫПОЛНЕНО"
            body = (
                f"{_public_label(user)} вернул сеть из {total_staked} агентов "
                "в составе "
                f"×{service.settings.death_operation_reward_multiplier} "
                "и получил бонус: "
                f"{bonus.emoji} {bonus.display_name}."
            )
            answer = "Операция успешна: состав удвоен, Tier 3 зачислен."
        else:
            title = "☠️ СВЯЗЬ ПОТЕРЯНА"
            body = (
                f"{_public_label(user)} отправил на задание всю сеть — "
                f"{total_staked} агентов. Никто не вернулся."
            )
            answer = "Операция провалена. Все поставленные агенты потеряны."
        await query.answer(answer, show_alert=True)
        logger.info(
            "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
            "outcome=%s stake=%s",
            result.event_id,
            chat.id,
            user.id,
            result.status.value,
            total_staked,
        )
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": title},
                {"type": "paragraph", "text": body},
            ],
            fallback_text=f"{title}\n{body}",
        )
    elif result.status is DeathOperationStatus.INSUFFICIENT_AGENTS:
        await query.answer("Для операции нужен хотя бы один агент.", show_alert=True)
    elif result.status is DeathOperationStatus.EXPIRED:
        await query.answer("Операция уже отменена Центром.", show_alert=True)
        await compact_event_query(query, "⌛ Событие закрыто: время истекло.")
    elif result.status is DeathOperationStatus.ALREADY_RESOLVED:
        await query.answer("Другой игрок уже принял операцию.", show_alert=True)
    else:
        await query.answer("Операция недоступна.", show_alert=True)
    return
