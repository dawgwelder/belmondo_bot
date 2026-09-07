"""Spy Clicker Telegram html5."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes
from config import logger
from spy_game.death_mission_repository import DeathMissionRun
from spy_game.models import DeadDropGameStatus, FindMoleGameStatus, InterceptGameStatus
from .context import _service
from .formatting import _display_name


async def spy_html5_game_launch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    message = query.message if query is not None else None
    webapp = context.bot_data.get("spy_webapp")
    if query is None:
        return
    if user is None or chat is None or message is None:
        await query.answer(
            "Запуск из inline-сообщения пока не поддерживается.",
            show_alert=True,
        )
        return
    if (
        webapp is None
        or not getattr(webapp, "game_enabled", False)
        or query.game_short_name != webapp.settings.game_short_name
    ):
        await query.answer(
            "HTML5-операция временно недоступна. Используйте текстовый вариант.",
            show_alert=True,
        )
        return
    try:
        service = _service(context)
        result = await service.start_html5_game(
            chat_id=chat.id,
            message_id=message.message_id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception(
            "spy_game: failed to start HTML5 operation chat_id=%s message_id=%s",
            chat.id,
            message.message_id,
        )
        await query.answer("Не удалось открыть операцию.", show_alert=True)
        return
    if isinstance(result, DeathMissionRun):
        if result.launch_token:
            await query.answer(url=webapp.game_launch_url(result.launch_token))
        else:
            await query.answer("Операция уже занята или недоступна.", show_alert=True)
        return
    if result.status in {
        InterceptGameStatus.READY,
        DeadDropGameStatus.READY,
        FindMoleGameStatus.READY,
    }:
        launch_url = webapp.game_launch_url(result.launch_token)
        if launch_url:
            await query.answer(url=launch_url)
            return
    if isinstance(result.status, FindMoleGameStatus):
        messages = {
            FindMoleGameStatus.ALREADY_PLAYED: "Вы уже выдвинули финальную версию.",
            FindMoleGameStatus.ALREADY_RESOLVED: "Крот уже раскрыт.",
            FindMoleGameStatus.EXPIRED: "Дело уже закрыто Центром.",
            FindMoleGameStatus.DISABLED: "HTML5-досье пока выключено.",
        }
        fallback = "Это дело больше недоступно."
    elif isinstance(result.status, DeadDropGameStatus):
        messages = {
            DeadDropGameStatus.ALREADY_PLAYED: "Вы уже использовали попытку.",
            DeadDropGameStatus.ALREADY_RESOLVED: "Тайник уже вскрыт.",
            DeadDropGameStatus.EXPIRED: "Тайник уже изъят Центром.",
            DeadDropGameStatus.DISABLED: "Разведсеть сейчас отключена.",
        }
        fallback = "Этот тайник больше недоступен."
    else:
        messages = {
            InterceptGameStatus.ALREADY_PLAYED: "Вы уже использовали попытку.",
            InterceptGameStatus.ALREADY_RESOLVED: "Канал уже перехвачен.",
            InterceptGameStatus.EXPIRED: "Канал уже замолчал.",
            InterceptGameStatus.DISABLED: "Разведсеть сейчас отключена.",
        }
        fallback = "Этот сигнал больше недоступен."
    await query.answer(
        messages.get(result.status, fallback),
        show_alert=True,
    )
