"""Telegram announcements after HTTP game settlement."""
from __future__ import annotations

from config import logger
from .telegram_messages import compact_event_message
from .models import DeadDropGameRun, FindMoleGameRun, InterceptGameRun
from .settings import AGENT_TYPES, ITEM_TYPES


async def announce_intercept_win(bot, result: InterceptGameRun) -> None:
    if result.chat_id is None or result.reward is None:
        return
    item = ITEM_TYPES.get(result.reward.reward_id or "")
    reward_text = (
        f"{item.emoji} {item.display_name} ×{result.reward.amount}"
        if item is not None
        else "награда Центра"
    )
    await compact_event_message(
        bot, result.chat_id, result.message_id, "✅ Перехват завершён."
    )
    try:
        await bot.send_message(
            chat_id=result.chat_id,
            text=(
                "✅ ШИФР РАСКРЫТ\n"
                f"{result.public_name or 'Скрытый агент'} восстановил канал "
                f"и получил {reward_text}."
            ),
        )
    except Exception:
        logger.exception(
            "spy_game: HTML5 intercept announcement failed event_id=%s",
            result.event_id,
        )


async def announce_dead_drop_win(bot, result: DeadDropGameRun) -> None:
    if result.chat_id is None or result.reward is None:
        return
    if result.reward.reward_type == "item":
        item = ITEM_TYPES.get(result.reward.reward_id or "")
        reward_text = (
            f"{item.emoji} {item.display_name} ×{result.reward.amount}"
            if item is not None
            else "предмет Центра"
        )
    elif result.reward.reward_type == "agent":
        agent = AGENT_TYPES.get(result.reward.reward_id or "")
        reward_text = (
            f"{agent.emoji} {agent.display_name} ×{result.reward.amount}"
            if agent is not None
            else "агент Центра"
        )
    else:
        reward_text = "ничего — тайник оказался пуст"
    await compact_event_message(
        bot, result.chat_id, result.message_id, "✅ Тайник вскрыт."
    )
    try:
        await bot.send_message(
            chat_id=result.chat_id,
            text=(
                "✅ ТАЙНИК ВСКРЫТ\n"
                f"{result.public_name or 'Скрытый агент'} подобрал код "
                f"и нашёл: {reward_text}."
            ),
        )
    except Exception:
        logger.exception(
            "spy_game: HTML5 dead drop announcement failed event_id=%s",
            result.event_id,
        )


async def announce_find_mole_win(bot, result: FindMoleGameRun) -> None:
    if (
        result.chat_id is None
        or result.item_reward is None
        or result.agent_reward is None
    ):
        return
    item = ITEM_TYPES.get(result.item_reward.reward_id or "")
    agent = AGENT_TYPES.get(result.agent_reward.agent_type)
    item_text = (
        f"{item.emoji} {item.display_name} ×{result.item_reward.amount}"
        if item is not None
        else "предмет Центра"
    )
    agent_text = (
        f"{agent.emoji} {agent.display_name} ×{result.agent_reward.amount}"
        if agent is not None
        else "агенты Tier 1"
    )
    await compact_event_message(
        bot, result.chat_id, result.message_id, "✅ Крот раскрыт."
    )
    try:
        await bot.send_message(
            chat_id=result.chat_id,
            text=(
                "✅ КРОТ РАСКРЫТ\n"
                f"{result.public_name or 'Скрытый агент'} завершил расследование "
                f"и получил {item_text} и {agent_text}."
            ),
        )
    except Exception:
        logger.exception(
            "spy_game: HTML5 mole announcement failed event_id=%s",
            result.event_id,
        )
