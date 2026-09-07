"""Spy Clicker Telegram publication."""
from __future__ import annotations

import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import logger
from spy_game.models import RecruitmentProgress, SpawnEvent
from spy_game.service import SpyGameService
from spy_game.settings import (
    DEFAULT_HANDLER_RECIPES,
    DEFAULT_INTERCEPT_SCENARIOS,
    DEFAULT_MOLE_CASES,
    DEFAULT_NPC_RECIPES,
)
from .context import _narrator
from .event_views import (
    _claim_keyboard,
    _event_keyboard,
    _html5_game_keyboard,
    _recruitment_message_text,
    build_event_blocks,
)
from .transport import _send_rich


async def _edit_recruitment_progress(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    service: SpyGameService,
    event_id: str,
) -> None:
    locks = context.bot_data.setdefault("spy_recruitment_edit_locks", {})
    lock = locks.setdefault(event_id, asyncio.Lock())
    async with lock:
        progress = await service.get_recruitment_progress(event_id)
        if progress is None:
            return
        current_text = getattr(getattr(query, "message", None), "text", None)
        source = current_text or "Обнаружены потенциальные связные."
        reply_markup = None if progress.completed else _claim_keyboard(event_id)
        try:
            await query.edit_message_text(
                text=_recruitment_message_text(source, progress),
                reply_markup=reply_markup,
            )
        except Exception:
            logger.warning(
                "spy_game: recruitment progress edit failed event_id=%s",
                event_id,
            )
            if progress.completed:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    logger.warning(
                        "spy_game: recruitment keyboard remained event_id=%s",
                        event_id,
                    )


async def publish_spy_event(
    context: ContextTypes.DEFAULT_TYPE,
    event: SpawnEvent,
) -> int:
    service = context.bot_data.get("spy_game")
    recipes = (
        service.settings.handler_recipes
        if isinstance(service, SpyGameService)
        else DEFAULT_HANDLER_RECIPES
    )
    is_handler = event.event_type == "handler"
    is_dead_drop = event.event_type == "dead_drop"
    is_death_operation = event.event_type == "death_operation"
    is_intercept = event.event_type == "intercept"
    is_cooperative = event.event_type == "cooperative_operation"
    is_chase = event.event_type == "chase"
    is_npc = event.event_type == "npc"
    is_mole = event.event_type == "find_mole"
    death_success_percent = (
        service.settings.death_operation_success_percent
        if isinstance(service, SpyGameService)
        else 35
    )
    death_reward_multiplier = (
        service.settings.death_operation_reward_multiplier
        if isinstance(service, SpyGameService)
        else 2
    )
    npc_reward_multiplier = (
        service.settings.npc_event_reward_multiplier
        if isinstance(service, SpyGameService)
        else 2
    )
    handler_reward_multiplier = (
        service.settings.handler_event_reward_multiplier
        if isinstance(service, SpyGameService)
        else 2
    )
    scenario = (
        service.settings.intercept_scenario(event.config_id or "")
        if isinstance(service, SpyGameService)
        else next(
            (
                candidate
                for candidate in DEFAULT_INTERCEPT_SCENARIOS
                if candidate.id == event.config_id
            ),
            None,
        )
    )
    mole_case = (
        service.settings.mole_case(event.config_id or "")
        if isinstance(service, SpyGameService)
        else next(
            (
                candidate
                for candidate in DEFAULT_MOLE_CASES
                if candidate.id == event.config_id
            ),
            None,
        )
    )
    cooperative_required = (
        service.settings.cooperative_required_contributions
        if isinstance(service, SpyGameService)
        else 3
    )
    recruitment_required = (
        service.settings.recruitment_winner_count
        if isinstance(service, SpyGameService)
        else 3
    )
    webapp = context.bot_data.get("spy_webapp")
    use_html5 = (
        (
            is_dead_drop
            or (is_intercept and scenario is not None)
            or (is_death_operation and event.config_id == "death_choice_v1")
        )
        and getattr(webapp, "game_enabled", False)
    ) or (
        is_mole
        and mole_case is not None
        and getattr(webapp, "mole_game_enabled", False)
    )
    if use_html5:
        try:
            message = await context.bot.send_game(
                chat_id=event.chat_id,
                game_short_name=webapp.settings.game_short_name,
                reply_markup=_html5_game_keyboard(event.event_type, event.event_id),
            )
            logger.info(
                "spy_narrative_selected event_id=%s event_type=%s source=html5_game",
                event.event_id,
                event.event_type,
            )
            return message.message_id
        except Exception:
            logger.exception(
                "spy_game: HTML5 game publication failed, using fallback "
                "event_id=%s event_type=%s",
                event.event_id,
                event.event_type,
            )
    if is_death_operation and event.config_id == "death_choice_v1":
        message = await context.bot.send_message(
            chat_id=event.chat_id,
            text=(
                "СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ\n\n"
                f"🎲 All-in: мгновенный исход, шанс {death_success_percent}%, "
                f"сеть ×{death_reward_multiplier} и Tier 3 ×1.\n\n"
                f"🕵️ Личная миссия: сеть ×{death_reward_multiplier} и Tier 3 ×2 "
                "или Tier 4 ×1 за полное прохождение. Есть аварийная эвакуация.\n\n"
                "В обоих режимах на кону вся доступная сеть. Списание — после подтверждения."
            ),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Открыть выбор режима",
                            callback_data=f"spy:deathmenu:{event.event_id}",
                        )
                    ]
                ]
            ),
        )
        return message.message_id
    narrative = await _narrator(context).narrate(event)
    if event.event_type == "recruitment":
        progress = RecruitmentProgress(
            event_id=event.event_id,
            claims=0,
            required_claims=recruitment_required,
        )
        message = await context.bot.send_message(
            chat_id=event.chat_id,
            text=_recruitment_message_text(narrative.body, progress),
            reply_markup=_claim_keyboard(event.event_id),
        )
        logger.info(
            "spy_narrative_selected event_id=%s source=%s",
            event.event_id,
            narrative.source,
        )
        return message.message_id
    message_id = await _send_rich(
        context,
        event.chat_id,
        build_event_blocks(
            event,
            narrative,
            death_success_percent=death_success_percent,
            death_reward_multiplier=death_reward_multiplier,
            intercept_prompt=scenario.prompt if scenario else None,
            cooperative_required=cooperative_required,
            recruitment_required=recruitment_required,
            handler_reward_multiplier=handler_reward_multiplier,
            npc_reward_multiplier=npc_reward_multiplier,
            mole_case=mole_case,
        ),
        fallback_text=(
            "🔎 Найти крота\nИзучите улики и выберите одного подозреваемого. "
            "У каждого игрока только одна финальная версия."
            if is_mole
            else "💀 Смертельная операция\n"
            f"Поставьте всех агентов: {death_success_percent}% на возврат состава "
            f"×{death_reward_multiplier} и бонус Tier 3."
            if is_death_operation
            else "📡 Перехват\nВыберите верную расшифровку сигнала."
            if is_intercept
            else f"🤝 Совместная операция\nНужно участников: {cooperative_required}."
            if is_cooperative
            else "🏎 Погоня\nНачните преследование, затем перехватите цель."
            if is_chase
            else "🗝 Специальный куратор\nРедкая встреча удваивает результат сделки."
            if is_npc
            else "🗂 Встреча с куратором\nРедкая встреча удваивает результат обмена."
            if is_handler
            else "📦 Тайник разведсети\nПервый обыскавший забирает содержимое."
            if is_dead_drop
            else "🚨 Сигнал разведсети\n"
            f"Замечены потенциальные связные. Первые {recruitment_required} "
            "разных пользователя получают агентов."
        ),
        reply_markup=_event_keyboard(
            event,
            recipes,
            (
                service.settings.intercept_scenarios
                if isinstance(service, SpyGameService)
                else DEFAULT_INTERCEPT_SCENARIOS
            ),
            (
                service.settings.npc_recipes
                if isinstance(service, SpyGameService)
                else DEFAULT_NPC_RECIPES
            ),
            handler_reward_multiplier,
            npc_reward_multiplier,
            (
                service.settings.mole_cases
                if isinstance(service, SpyGameService)
                else DEFAULT_MOLE_CASES
            ),
        ),
    )
    logger.info(
        "spy_narrative_selected event_id=%s source=%s",
        event.event_id,
        narrative.source,
    )
    return message_id
