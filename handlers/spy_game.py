"""Telegram adapter for the persistent Spy Clicker game."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import logger
from guards import pause
from spy_game.models import ClaimStatus, Profile, SpawnEvent
from spy_game.narrator import EventNarrative, Narrator, TemplateNarrator
from spy_game.service import SpyGameService
from spy_game.settings import AGENT_TYPES
from telegram_utils import send_rich_message

SPY_CALLBACK_PATTERN = r"^spy:(?:menu|claim):[a-z0-9_]{1,24}$"


def _service(context: ContextTypes.DEFAULT_TYPE) -> SpyGameService:
    service = context.bot_data.get("spy_game")
    if not isinstance(service, SpyGameService):
        raise RuntimeError("Spy Game service is unavailable")
    return service


def _narrator(context: ContextTypes.DEFAULT_TYPE) -> Narrator:
    narrator = context.bot_data.get("spy_narrator")
    return narrator if narrator is not None else TemplateNarrator()


def _markup_payload(markup: InlineKeyboardMarkup | None) -> dict | None:
    if markup is None:
        return None
    return markup.to_dict()


async def _send_rich(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    blocks: list[dict],
    *,
    fallback_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    try:
        payload = await send_rich_message(
            context.bot.token,
            chat_id,
            blocks,
            reply_markup=_markup_payload(reply_markup),
        )
        return int(payload["result"]["message_id"])
    except Exception:
        logger.exception("spy_game: sendRichMessage failed chat_id=%s", chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
            reply_markup=reply_markup,
        )
        return message.message_id


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🪪 Досье", callback_data="spy:menu:profile"),
                InlineKeyboardButton("🕵️ Агенты", callback_data="spy:menu:agents"),
            ],
            [
                InlineKeyboardButton(
                    "⏱ Следующее событие", callback_data="spy:menu:status"
                ),
                InlineKeyboardButton("🔄 Обновить", callback_data="spy:menu:refresh"),
            ],
        ]
    )


def _claim_keyboard(event_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Завербовать", callback_data=f"spy:claim:{event_id}")]]
    )


def _display_name(user) -> str:
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


def _countdown(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "таймер ещё не назначен"
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        return "событие ожидается в ближайший цикл"
    minutes = max(1, math.ceil(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"примерно через {hours} ч {minutes} мин"
    return f"примерно через {minutes} мин"


def build_menu_blocks(profile: Profile, status, now: datetime) -> list[dict]:
    if status.active_event_id:
        event_line = "В чате уже идёт операция — ищите сообщение с кнопкой."
    elif status.enabled:
        event_line = f"Следующий сигнал: {_countdown(status.next_event_at, now)}."
    else:
        event_line = "В этом чате сеть пока не активирована."
    return [
        {"type": "paragraph", "text": "🕵️ SPY CLICKER · ОПЕРАТИВНЫЙ ЦЕНТР"},
        {
            "type": "details",
            "summary": "Ваше досье",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Агентов: {profile.total_agents}\n"
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}"
                    ),
                }
            ],
        },
        {
            "type": "paragraph",
            "text": (f"Активность чата: {status.activity_score:.1f}\n{event_line}"),
        },
        {
            "type": "footer",
            "text": "Сеть просыпается от разговоров. Чем живее чат, тем ближе событие.",
        },
    ]


def build_profile_blocks(profile: Profile) -> list[dict]:
    name = profile.display_name or profile.username or str(profile.user_id)
    return [
        {"type": "paragraph", "text": f"🪪 ДОСЬЕ · {name}"},
        {
            "type": "details",
            "summary": "Показатели",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}\n"
                        f"Агентов в сети: {profile.total_agents}"
                    ),
                }
            ],
        },
        {"type": "footer", "text": "Никаких лишних имён. Только результаты."},
    ]


def build_agents_blocks(holdings) -> list[dict]:
    by_tier: dict[int, list[str]] = {}
    for holding in holdings:
        agent = AGENT_TYPES.get(holding.agent_type)
        if agent is None:
            continue
        by_tier.setdefault(agent.tier, []).append(
            f"{agent.emoji} {agent.display_name}: {holding.amount}"
        )
    blocks: list[dict] = [{"type": "paragraph", "text": "🕵️ АГЕНТУРНАЯ СЕТЬ"}]
    if not by_tier:
        blocks.append(
            {
                "type": "paragraph",
                "text": "Сеть пока пуста. Первым замечайте события в чате.",
            }
        )
    else:
        for tier in sorted(by_tier):
            blocks.append(
                {
                    "type": "details",
                    "summary": f"Уровень {tier}",
                    "blocks": [{"type": "paragraph", "text": "\n".join(by_tier[tier])}],
                }
            )
    blocks.append(
        {"type": "footer", "text": "Обычные агенты хранятся общим количеством."}
    )
    return blocks


def build_status_blocks(status, now: datetime) -> list[dict]:
    if status.active_event_id:
        state = f"Активная операция: {status.active_event_id}"
        timer = f"Окно закроется: {_countdown(status.active_event_expires_at, now)}"
    elif status.enabled:
        state = "Сеть активна"
        timer = f"Следующий сигнал: {_countdown(status.next_event_at, now)}"
    else:
        state = "Сеть не активирована в этом чате"
        timer = "Таймер остановлен"
    return [
        {"type": "paragraph", "text": "⏱ СОСТОЯНИЕ СЕТИ"},
        {
            "type": "paragraph",
            "text": f"{state}\nАктивность: {status.activity_score:.1f}\n{timer}",
        },
        {
            "type": "footer",
            "text": "Порог запуска — 6 очков; со временем активность затухает.",
        },
    ]


def build_event_blocks(
    event: SpawnEvent,
    narrative: EventNarrative,
) -> list[dict]:
    lifetime_minutes = max(
        1,
        math.ceil((event.expires_at - datetime.now(timezone.utc)).total_seconds() / 60),
    )
    return [
        {"type": "paragraph", "text": "🚨 СИГНАЛ РАЗВЕДСЕТИ"},
        {"type": "paragraph", "text": narrative.body},
        {
            "type": "footer",
            "text": f"Первый подтверждённый контакт получит агента. Окно: ~{lifetime_minutes} мин.",
        },
    ]


async def _profile_for_update(update: Update, service: SpyGameService) -> Profile:
    user = update.effective_user
    return await service.get_profile(
        user_id=user.id,
        username=user.username,
        display_name=_display_name(user),
    )


async def _send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    profile = await _profile_for_update(update, service)
    status = await service.get_chat_status(update.effective_chat.id)
    now = datetime.now(timezone.utc)
    await _send_rich(
        context,
        update.effective_chat.id,
        build_menu_blocks(profile, status, now),
        fallback_text=(
            "🕵️ Spy Clicker\n"
            f"Агентов: {profile.total_agents}\n"
            f"Активность: {status.activity_score:.1f}\n"
            f"Следующее событие: {_countdown(status.next_event_at, now)}"
        ),
        reply_markup=_menu_keyboard(),
    )


@pause
async def spy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    await _send_menu(update, context)


async def publish_spy_event(
    context: ContextTypes.DEFAULT_TYPE,
    event: SpawnEvent,
) -> int:
    narrative = await _narrator(context).narrate(event)
    message_id = await _send_rich(
        context,
        event.chat_id,
        build_event_blocks(event, narrative),
        fallback_text=(
            "🚨 Сигнал разведсети\n"
            "Замечен потенциальный связной. Первый контакт получает агента."
        ),
        reply_markup=_claim_keyboard(event.event_id),
    )
    logger.info(
        "spy_narrative_selected event_id=%s source=%s",
        event.event_id,
        narrative.source,
    )
    return message_id


async def _remove_event_keyboard(context, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    except Exception:
        logger.warning(
            "spy_game: failed to remove keyboard chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )


async def spy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if query is None or user is None or chat is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "spy":
        await query.answer("Некорректный сигнал.", show_alert=True)
        return
    service = _service(context)
    category, value = parts[1], parts[2]

    if category == "menu":
        if value not in {"refresh", "profile", "agents", "status"}:
            await query.answer("Неизвестный пункт меню.", show_alert=True)
            return
        await query.answer()
        if value == "refresh":
            await _send_menu(update, context)
            return
        if value == "profile":
            profile = await _profile_for_update(update, service)
            blocks = build_profile_blocks(profile)
            fallback = (
                f"Досье: репутация {profile.reputation}, "
                f"агентов {profile.total_agents}."
            )
        elif value == "agents":
            await _profile_for_update(update, service)
            holdings = await service.get_agents(user.id)
            blocks = build_agents_blocks(holdings)
            fallback = "Агентурная сеть: " + (
                ", ".join(f"{item.agent_type} ×{item.amount}" for item in holdings)
                or "пока пуста"
            )
        elif value == "status":
            status = await service.get_chat_status(chat.id)
            now = datetime.now(timezone.utc)
            blocks = build_status_blocks(status, now)
            fallback = (
                f"Активность: {status.activity_score:.1f}. "
                f"Следующее событие: {_countdown(status.next_event_at, now)}."
            )
        await _send_rich(context, chat.id, blocks, fallback_text=fallback)
        return

    if category != "claim":
        await query.answer("Неизвестное действие.", show_alert=True)
        return
    try:
        result = await service.claim_event(
            event_id=value,
            action="claim",
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: claim failed event_id=%s", value)
        await query.answer(
            "Связь с центром потеряна. Попробуйте ещё раз.", show_alert=True
        )
        return

    if result.status is ClaimStatus.WON:
        agent = AGENT_TYPES[result.reward.agent_type]
        await query.answer(
            f"Контакт установлен: {agent.display_name} ×{result.reward.amount}",
            show_alert=True,
        )
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            logger.warning("spy_game: winner could not remove event keyboard")
        await _send_rich(
            context,
            chat.id,
            [
                {"type": "paragraph", "text": "✅ КОНТАКТ УСТАНОВЛЕН"},
                {
                    "type": "paragraph",
                    "text": (
                        f"{_display_name(user)} первым вышел на связного.\n"
                        f"Награда: {agent.emoji} {agent.display_name} ×{result.reward.amount}"
                    ),
                },
                {
                    "type": "footer",
                    "text": "Операция закрыта. Следующий сигнал придёт позже.",
                },
            ],
            fallback_text=(
                f"✅ {_display_name(user)} получает: "
                f"{agent.display_name} ×{result.reward.amount}."
            ),
        )
    elif result.status is ClaimStatus.EXPIRED:
        await query.answer("Окно контакта уже закрылось.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    elif result.status is ClaimStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент оказался быстрее.", show_alert=True)
    elif result.status is ClaimStatus.DISABLED:
        await query.answer("Разведсеть сейчас отключена.", show_alert=True)
    else:
        await query.answer("Этот сигнал больше недействителен.", show_alert=True)


async def track_spy_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if (
        user is None
        or user.is_bot
        or chat is None
        or chat.type not in {"group", "supergroup"}
        or context.bot_data.get("paused", False)
    ):
        return
    await _service(context).record_activity(chat.id, user.id)


async def spy_game_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    try:
        result = await service.tick()
    except Exception:
        logger.exception("spy_game: scheduler tick failed")
        return
    for expired in result.expired:
        await _remove_event_keyboard(
            context,
            expired.chat_id,
            expired.message_id,
        )
    for event in result.spawned:
        try:
            message_id = await publish_spy_event(context, event)
            await service.attach_message(event.event_id, message_id)
            logger.info(
                "spy_event_created event_id=%s chat_id=%s event_type=%s expires_at=%s",
                event.event_id,
                event.chat_id,
                event.event_type,
                event.expires_at.isoformat(),
            )
        except Exception:
            logger.exception(
                "spy_game: event publication failed event_id=%s", event.event_id
            )
            await service.cancel_publication(event.event_id)


async def spy_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    if update.effective_user.id != context.bot_data.get("master"):
        await update.effective_message.reply_text("Команда доступна только master.")
        return
    service = _service(context)
    action = context.args[0].lower() if context.args else "status"
    chat_id = update.effective_chat.id
    if action in {"enable", "spawn"} and update.effective_chat.type not in {
        "group",
        "supergroup",
    }:
        await update.effective_message.reply_text(
            "Эта операция доступна только в group/supergroup."
        )
        return
    if action == "enable":
        result = await service.enable_chat(chat_id)
    elif action == "disable":
        result = await service.disable_chat(chat_id)
        await _remove_event_keyboard(context, chat_id, result.message_id_to_close)
    elif action == "spawn":
        result = await service.manual_spawn(chat_id)
        if result.event is not None:
            try:
                message_id = await publish_spy_event(context, result.event)
                await service.attach_message(result.event.event_id, message_id)
            except Exception:
                logger.exception(
                    "spy_game: manual publication failed event_id=%s",
                    result.event.event_id,
                )
                await service.cancel_publication(result.event.event_id)
                result = type(result)(False, "Не удалось опубликовать событие.")
    elif action == "status":
        status = await service.get_chat_status(chat_id)
        now = datetime.now(timezone.utc)
        blocks = build_status_blocks(status, now)
        blocks.insert(
            -1,
            {
                "type": "details",
                "summary": "Runtime",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": (
                            "Narrator: "
                            + (
                                "LLM + template fallback"
                                if service.settings.llm_narrator_enabled
                                else "template"
                            )
                        ),
                    }
                ],
            },
        )
        await _send_rich(
            context,
            chat_id,
            blocks,
            fallback_text=(
                f"enabled={status.enabled}, activity={status.activity_score:.1f}, "
                f"next={status.next_event_at}, active={status.active_event_id}"
            ),
        )
        return
    else:
        await update.effective_message.reply_text(
            "Использование: /spy_admin enable|disable|spawn|status"
        )
        return
    await update.effective_message.reply_text(result.message)
