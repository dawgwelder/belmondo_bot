"""Persisted chase timers and serialized Telegram updates."""
import asyncio
from datetime import timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from config import logger
from spy_game.chase_rules import CHASE_SECONDS
from spy_game.models import ChaseStatus
from spy_game.settings import AGENT_TYPES, ITEM_TYPES
from .context import _service
from .formatting import _display_name


def chase_text(result):
    rewards = []
    for reward in result.rewards:
        catalog = AGENT_TYPES if reward.reward_type == "agent" else ITEM_TYPES
        item = catalog[reward.reward_id]
        rewards.append(f"{item.emoji} {item.display_name} ×{reward.amount}")
    prize = ", ".join(rewards)
    if result.status is ChaseStatus.COMPLETED:
        return f"✅ Погоня завершена. {result.leader_name} забирает приз:\n{prize}"
    deadline = result.deadline.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
    limit = (
        "Лимит ходов достигнут. Ждём финиша."
        if result.turn == len(CHASE_SECONDS)
        else "Другой игрок может перехватить цель и увеличить приз."
    )
    return (
        f"🏎 Погоня · ход {result.turn}/{len(CHASE_SECONDS)}\n"
        f"Лидер: {result.leader_name}\nПриз: {prize}\n"
        f"Финиш: {deadline} · таймер хода {result.hold_seconds} сек.\n{limit}"
    )


def chase_lock(context, event_id):
    return context.bot_data.setdefault("spy_chase_edit_locks", {}).setdefault(
        event_id, asyncio.Lock()
    )


async def render_chase(context, service, result):
    if result.message_id is None:
        return
    keyboard = None
    if result.status is ChaseStatus.STARTED and result.turn < len(CHASE_SECONDS):
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Перехватить цель", callback_data=f"spy:chase:{result.event_id}"
                    )
                ]
            ]
        )
    try:
        await context.bot.edit_message_text(
            chat_id=result.chat_id,
            message_id=result.message_id,
            text=chase_text(result),
            reply_markup=keyboard,
        )
    except BadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    if result.status is ChaseStatus.COMPLETED:
        await service.mark_chase_notified(result.event_id)


async def chase_tick(context):
    service = _service(context)
    try:
        results = await service.settle_chases()
        for result in results:
            try:
                async with chase_lock(context, result.event_id):
                    await render_chase(
                        context, service, await service.get_chase(result.event_id)
                    )
            except Exception:
                logger.exception(
                    "spy_chase: result delivery failed event_id=%s", result.event_id
                )
    except Exception:
        logger.exception("spy_chase: timer failed")


async def handle_chase(update, context, category, value):
    service = _service(context)
    query, user, chat = (
        update.callback_query,
        update.effective_user,
        update.effective_chat,
    )
    async with chase_lock(context, value):
        try:
            result = await service.advance_chase(
                event_id=value,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
            messages = {
                ChaseStatus.STARTED: "Вы лидируете. Удержите цель до конца таймера!",
                ChaseStatus.ALREADY_LEADING: "Вы уже лидер. Перехватить может другой игрок.",
                ChaseStatus.LIMIT_REACHED: "Лимит ходов достигнут. Ждём финиша.",
                ChaseStatus.COMPLETED: "Время вышло. Приз получил последний лидер.",
                ChaseStatus.ALREADY_RESOLVED: "Погоня уже завершена.",
                ChaseStatus.EXPIRED: "Цель ушла от преследования.",
            }
            await query.answer(
                messages.get(result.status, "Погоня недоступна."), show_alert=True
            )
            if result.status in {
                ChaseStatus.STARTED,
                ChaseStatus.COMPLETED,
                ChaseStatus.ALREADY_LEADING,
                ChaseStatus.LIMIT_REACHED,
            }:
                # Re-read: a timer could have settled the event while answering Telegram.
                await render_chase(context, service, await service.get_chase(value))
        except Exception:
            logger.exception("spy_chase: callback failed event_id=%s", value)
