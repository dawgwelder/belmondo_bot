"""Spy Clicker Telegram callback contacts."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.telegram_messages import compact_event_query, publish_event_result
from spy_game.models import EconomyStatus, NpcStatus
from spy_game.settings import AGENT_TYPES, ITEM_TYPES
from .context import _service
from .formatting import _display_name, _format_costs, _format_item_costs, _public_label


async def handle_npc(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    recipe_id = category.removeprefix("npc_")
    try:
        result = await service.interact_with_npc(
            event_id=value,
            recipe_id=recipe_id,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: NPC interaction failed event_id=%s", value)
        await query.answer(
            "Специальный канал прервался. Ресурсы не изменены.",
            show_alert=True,
        )
        return
    if result.status is NpcStatus.SUCCESS:
        reward = result.reward
        if reward.reward_type == "agent":
            definition = AGENT_TYPES[reward.reward_id]
            reward_text = (
                f"{definition.emoji} {definition.display_name} ×{reward.amount}"
            )
        else:
            definition = ITEM_TYPES[reward.reward_id]
            reward_text = (
                f"{definition.emoji} {definition.display_name} ×{reward.amount}"
            )
        await query.answer(f"Сделка завершена: {reward_text}", show_alert=True)
        await publish_event_result(
            context.bot,
            chat.id,
            query.message.message_id,
            f"✅ СПЕЦИАЛЬНАЯ СДЕЛКА\n{_public_label(user)} получил: {reward_text}.",
        )
    elif result.status is NpcStatus.INSUFFICIENT_RESOURCES:
        requirements = []
        if result.required_agents:
            requirements.append(_format_costs(result.required_agents))
        if result.required_items:
            requirements.append(_format_item_costs(result.required_items))
        await query.answer(
            ("Нужны новые ненадетые предметы. Для сделки: " + "; ".join(requirements))[
                :195
            ],
            show_alert=True,
        )
    elif result.status is NpcStatus.EXPIRED:
        await query.answer("Специальный канал уже закрыт.", show_alert=True)
        await compact_event_query(query, "⌛ Событие закрыто: время истекло.")
    elif result.status is NpcStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент уже завершил сделку.", show_alert=True)
    else:
        await query.answer("Эта сделка недоступна.", show_alert=True)
    return


async def handle_exchange(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, value: str
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    service = _service(context)
    recipe_id = category.removeprefix("exchange_")
    try:
        result = await service.exchange_with_handler(
            event_id=value,
            recipe_id=recipe_id,
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: exchange failed event_id=%s", value)
        await query.answer(
            "Куратор не подтвердил обмен. Попробуйте ещё раз.", show_alert=True
        )
        return
    if result.status is EconomyStatus.SUCCESS:
        agent = AGENT_TYPES[result.reward.agent_type]
        await query.answer(
            f"Обмен завершён: {agent.display_name} ×{result.reward.amount}",
            show_alert=True,
        )
        logger.info(
            "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
            "reward_id=%s reward_amount=%s",
            result.event_id,
            chat.id,
            user.id,
            result.reward.agent_type,
            result.reward.amount,
        )
        await publish_event_result(
            context.bot,
            chat.id,
            query.message.message_id,
            f"✅ ОБМЕН ЗАВЕРШЁН\n"
            f"{_public_label(user)} первым предъявил ресурсы.\n"
            f"Новый агент: {agent.emoji} {agent.display_name} "
            f"×{result.reward.amount}\nКуратор закрыл дипломат.",
        )
    elif result.status is EconomyStatus.INSUFFICIENT_RESOURCES:
        await query.answer(
            "Для обмена нужно: " + _format_costs(result.required),
            show_alert=True,
        )
    elif result.status is EconomyStatus.EXPIRED:
        await query.answer("Куратор уже ушёл.", show_alert=True)
        await compact_event_query(query, "⌛ Встреча с куратором: время истекло.")
    elif result.status is EconomyStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент уже завершил обмен.", show_alert=True)
    else:
        await query.answer("Этот обмен недоступен.", show_alert=True)
    return
