"""Telegram orchestration for LLM-led group games."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import TELEGRAM_MAX_MESSAGE_LENGTH, logger
from games import (
    DuplicateSubmission,
    GameAlreadyActive,
    GameFull,
    GameNotReady,
    GamePhase,
    GameState,
    GameStore,
    InvalidSubmission,
)
from games.scenarios import SCENARIOS, GameScenario
from guards import ensure_master_in_chat_for_ai, pause
from state import ensure_chat_state, remember_chat_user

GAME_CALLBACK_PATTERN = r"^game:"
LOBBY_TIMEOUT_SECONDS = 180
ROUND_TIMEOUT_SECONDS = 300
LLM_CALL_TIMEOUT_SECONDS = 45
PLAYER_NAME_MAX_LENGTH = 64


def _store(context: ContextTypes.DEFAULT_TYPE) -> GameStore:
    return GameStore(ensure_chat_state(context)["llm_games"])


def _sessions(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, Any]]:
    return ensure_chat_state(context)["llm_game_sessions"]


def _lock(context: ContextTypes.DEFAULT_TYPE) -> asyncio.Lock:
    return ensure_chat_state(context)["llm_game_lock"]


def _scenario(game: GameState) -> GameScenario:
    return SCENARIOS[game.game_type]


def _compact(value: str, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


def _telegram_text(value: str) -> str:
    if len(value) <= TELEGRAM_MAX_MESSAGE_LENGTH:
        return value
    return value[: TELEGRAM_MAX_MESSAGE_LENGTH - 1] + "…"


def _display_name(user) -> str:
    value = f"@{user.username}" if getattr(user, "username", None) else user.full_name
    return _compact(value, PLAYER_NAME_MAX_LENGTH)


def _player_name(session: dict[str, Any], user_id: int) -> str:
    return _compact(session["players"][user_id]["name"], PLAYER_NAME_MAX_LENGTH)


def _status(submitted: dict[int, str], user_id: int) -> str:
    return "ответ получен" if user_id in submitted else "ожидается ответ"


def _keyboard(game: GameState) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Участвовать",
                    callback_data=f"game:{game.game_type}:{game.game_id}:join",
                )
            ],
            [
                InlineKeyboardButton(
                    "Начать",
                    callback_data=f"game:{game.game_type}:{game.game_id}:start",
                ),
                InlineKeyboardButton(
                    "Отмена",
                    callback_data=f"game:{game.game_type}:{game.game_id}:cancel",
                ),
            ],
        ]
    )


def _phase_round(game: GameState) -> int:
    if game.phase is GamePhase.ROUND_ONE:
        return 1
    if game.phase is GamePhase.ROUND_TWO:
        return 2
    return 0


def _snapshot(game: GameState, session: dict[str, Any]) -> dict[str, Any]:
    submissions = game.submissions
    moves: dict[str, dict[str, str]] = {}
    for user_id, answer in submissions[GamePhase.ROUND_ONE].items():
        moves.setdefault(str(user_id), {})["1"] = answer
    for user_id, answer in submissions[GamePhase.ROUND_TWO].items():
        moves.setdefault(str(user_id), {})["2"] = answer

    return {
        "id": game.game_id,
        "type": game.game_type,
        "status": "round"
        if game.phase in (GamePhase.ROUND_ONE, GamePhase.ROUND_TWO)
        else game.phase.value,
        "round": _phase_round(game),
        "creator_id": game.creator_id,
        "chat_id": game.chat_id,
        "round_message_id": game.current_prompt_message_id,
        "players": {
            str(user_id): {"id": user_id, "name": _player_name(session, user_id)}
            for user_id in game.participants
        },
        "player_order": list(game.participants),
        "moves": moves,
        "content": session["content"],
        "active_player_ids": list(game.participants),
        "timeout_job_name": game.timeout_job_name,
    }


def _lobby_text(game: GameState, session: dict[str, Any]) -> str:
    scenario = _scenario(game)
    names = "\n".join(
        f"• {_player_name(session, user_id)}" for user_id in game.participants
    )
    return _telegram_text(
        f"{scenario.title}\n\n"
        f"{scenario.lobby_intro}\n\n"
        f"Участники ({len(game.participants)}/{game.MAX_PARTICIPANTS}, "
        f"минимум {game.MIN_PARTICIPANTS}):\n{names or '—'}\n\n"
        "Начать может автор лобби, когда все готовы."
    )


def _round_status_text(game: GameState, session: dict[str, Any]) -> str:
    submissions = game.submissions[game.phase]
    lines = [
        f"• {_player_name(session, user_id)}: {_status(submissions, user_id)}"
        for user_id in game.participants
    ]
    return "\n".join(lines)


def _round_one_text(
    game: GameState, session: dict[str, Any], opening: dict[str, Any]
) -> str:
    body = _scenario(game).format_opening_message(_snapshot(game, session), opening)
    return _telegram_text(f"{body}\n\n{_round_status_text(game, session)}")


def _round_two_text(
    game: GameState, session: dict[str, Any], payload: dict[str, Any]
) -> str:
    body = _scenario(game).format_round_two_message(_snapshot(game, session), payload)
    return _telegram_text(f"{body}\n\n{_round_status_text(game, session)}")


def _verdict_text(
    game: GameState, session: dict[str, Any], verdict: dict[str, Any]
) -> str:
    return _telegram_text(
        _scenario(game).format_verdict_message(_snapshot(game, session), verdict)
    )


def _is_current(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    expected_phase: GamePhase | None = None,
) -> bool:
    current = _store(context).get(game.chat_id)
    return (
        current is game
        and current.game_id == game.game_id
        and (expected_phase is None or current.phase is expected_phase)
    )


def _find_by_id(
    context: ContextTypes.DEFAULT_TYPE, game_id: str
) -> tuple[GameState, dict[str, Any]] | None:
    chat_games = ensure_chat_state(context)["llm_games"]
    sessions = _sessions(context)
    for game in chat_games.values():
        if game.game_id == game_id:
            session = sessions.get(game.game_id)
            if session is not None:
                return game, session
    return None


def _reserve_operation(session: dict[str, Any], operation: str) -> str:
    token = secrets.token_hex(8)
    session["operation"] = operation
    session["operation_token"] = token
    return token


def _operation_matches(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    token: str,
    expected_phase: GamePhase,
) -> bool:
    return (
        _is_current(context, game, expected_phase)
        and _sessions(context).get(game.game_id) is session
        and session.get("operation_token") == token
    )


def _clear_operation(session: dict[str, Any], token: str) -> None:
    if session.get("operation_token") == token:
        session["operation"] = None
        session["operation_token"] = None


def _cancel_timeout(context: ContextTypes.DEFAULT_TYPE, game: GameState) -> None:
    name = game.timeout_job_name
    job_queue = getattr(context, "job_queue", None)
    if name and job_queue is not None:
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()
    game.clear_timeout()


def _schedule_timeout(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    seconds: int,
) -> bool:
    old_name = game.timeout_job_name
    name = (
        f"game-timeout:{game.chat_id}:{game.game_id}:{game.phase.value}:"
        f"{secrets.token_hex(3)}"
    )
    deadline = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None:
        logger.error("games: JobQueue unavailable game=%s", game.game_id)
        return False
    try:
        job_queue.run_once(
            llm_game_timeout,
            seconds,
            data={
                "chat_id": game.chat_id,
                "game_id": game.game_id,
                "phase": game.phase.value,
                "job_name": name,
            },
            chat_id=game.chat_id,
            name=name,
        )
    except Exception:
        logger.exception("games: timeout scheduling failed game=%s", game.game_id)
        return False
    if old_name:
        for job in job_queue.get_jobs_by_name(old_name):
            job.schedule_removal()
    game.set_timeout(name, deadline)
    return True


def _remove_game(context: ContextTypes.DEFAULT_TYPE, game: GameState) -> bool:
    if not _is_current(context, game):
        _cancel_timeout(context, game)
        _sessions(context).pop(game.game_id, None)
        return False
    _cancel_timeout(context, game)
    if _store(context).remove(game.chat_id, expected=game) is None:
        return False
    _sessions(context).pop(game.game_id, None)
    return True


async def _send_abort_message(
    context: ContextTypes.DEFAULT_TYPE, game: GameState, reason: str
) -> None:
    try:
        await context.bot.send_message(
            chat_id=game.chat_id,
            text=_telegram_text(
                f"{_scenario(game).title} завершена без победителя. {reason}"
            ),
        )
    except Exception:
        logger.exception("games: failed to publish abort game=%s", game.game_id)


async def _finish_without_winner(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    reason: str,
    *,
    session: dict[str, Any] | None = None,
    token: str | None = None,
    expected_phase: GamePhase | None = None,
) -> None:
    async with _lock(context):
        if token is not None and (
            session is None
            or expected_phase is None
            or not _operation_matches(context, game, session, token, expected_phase)
        ):
            return
        removed = _remove_game(context, game)
    if removed:
        await _send_abort_message(context, game, reason)


async def _delete_stale_message(
    context: ContextTypes.DEFAULT_TYPE, game: GameState, message
) -> None:
    try:
        await context.bot.delete_message(
            chat_id=game.chat_id,
            message_id=message.message_id,
        )
    except Exception:
        logger.exception(
            "games: failed to delete stale message game=%s message=%s",
            game.game_id,
            getattr(message, "message_id", None),
        )


async def _with_model_timeout(awaitable):
    return await asyncio.wait_for(awaitable, timeout=LLM_CALL_TIMEOUT_SECONDS)


async def _run_user_continuation(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    token: str,
    expected_phase: GamePhase,
    operation: str,
    continuation,
) -> None:
    try:
        await continuation
    except asyncio.CancelledError:
        logger.info(
            "games: background operation cancelled game=%s operation=%s",
            game.game_id,
            operation,
        )
        try:
            await _finish_without_winner(
                context,
                game,
                "Фоновая операция была остановлена.",
                session=session,
                token=token,
                expected_phase=expected_phase,
            )
        except Exception:
            logger.exception("games: cancelled-operation cleanup failed")
        raise
    except Exception:
        logger.exception(
            "games: background operation failed game=%s operation=%s",
            game.game_id,
            operation,
        )
        try:
            await _finish_without_winner(
                context,
                game,
                "Фоновая операция завершилась с ошибкой.",
                session=session,
                token=token,
                expected_phase=expected_phase,
            )
        except Exception:
            logger.exception("games: background failure cleanup failed")


def _schedule_user_continuation(
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
    game: GameState,
    session: dict[str, Any],
    token: str,
    expected_phase: GamePhase,
    operation: str,
    continuation,
) -> bool:
    application = getattr(context, "application", None)
    if application is None:
        logger.error("games: Application unavailable for background task")
        continuation.close()
        return False
    managed = _run_user_continuation(
        context,
        game,
        session,
        token,
        expected_phase,
        operation,
        continuation,
    )
    try:
        application.create_task(
            managed,
            update=update,
            name=f"game:{game.chat_id}:{game.game_id}:{operation}:{token}",
        )
    except Exception:
        managed.close()
        continuation.close()
        logger.exception("games: failed to schedule background task")
        return False
    return True


async def _prepare_operation(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    expected_phase: GamePhase,
    operation: str,
    token: str | None,
) -> str | None:
    async with _lock(context):
        if token is not None:
            return (
                token
                if _operation_matches(context, game, session, token, expected_phase)
                else None
            )
        if not _is_current(context, game, expected_phase):
            return None
        return _reserve_operation(session, operation)


async def _start_round_one(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    token: str | None = None,
) -> None:
    token = await _prepare_operation(
        context, game, session, GamePhase.ROUND_ONE, "opening_generation", token
    )
    if token is None:
        return
    scenario = _scenario(game)
    try:
        opening = await _with_model_timeout(
            scenario.generate_opening(_snapshot(game, session))
        )
        if opening is None:
            raise RuntimeError("model returned invalid opening")
    except Exception:
        logger.exception("games: opening generation failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Гейм-мастер не смог подготовить корректный первый раунд.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_ONE,
        )
        return

    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.ROUND_ONE):
            return
        session["content"]["opening"] = opening

    try:
        prompt = await context.bot.send_message(
            chat_id=game.chat_id, text=_round_one_text(game, session, opening)
        )
    except Exception:
        logger.exception("games: round-one prompt failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Не удалось опубликовать первый раунд.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_ONE,
        )
        return

    stale_message = False
    schedule_failed = False
    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.ROUND_ONE):
            stale_message = True
        else:
            game.set_prompt_message(prompt.message_id)
            schedule_failed = not _schedule_timeout(context, game, ROUND_TIMEOUT_SECONDS)
            if not schedule_failed:
                _clear_operation(session, token)
    if stale_message:
        await _delete_stale_message(context, game, prompt)
    elif schedule_failed:
        await _finish_without_winner(
            context,
            game,
            "Не удалось установить таймер первого раунда.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_ONE,
        )


async def _start_round_two(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    token: str | None = None,
) -> None:
    token = await _prepare_operation(
        context, game, session, GamePhase.ROUND_TWO, "round_two_generation", token
    )
    if token is None:
        return
    scenario = _scenario(game)
    try:
        payload = await _with_model_timeout(
            scenario.generate_round_two(_snapshot(game, session))
        )
        if payload is None:
            raise RuntimeError("model returned invalid round two")
    except Exception:
        logger.exception("games: round-two generation failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Гейм-мастер не смог сформулировать второй раунд.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_TWO,
        )
        return

    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.ROUND_TWO):
            return
        session["content"]["round_two"] = payload

    try:
        prompt = await context.bot.send_message(
            chat_id=game.chat_id, text=_round_two_text(game, session, payload)
        )
    except Exception:
        logger.exception("games: round-two prompt failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Не удалось опубликовать второй раунд.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_TWO,
        )
        return

    stale_message = False
    schedule_failed = False
    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.ROUND_TWO):
            stale_message = True
        else:
            game.set_prompt_message(prompt.message_id)
            schedule_failed = not _schedule_timeout(context, game, ROUND_TIMEOUT_SECONDS)
            if not schedule_failed:
                _clear_operation(session, token)
    if stale_message:
        await _delete_stale_message(context, game, prompt)
    elif schedule_failed:
        await _finish_without_winner(
            context,
            game,
            "Не удалось установить таймер второго раунда.",
            session=session,
            token=token,
            expected_phase=GamePhase.ROUND_TWO,
        )


async def _publish_verdict(
    context: ContextTypes.DEFAULT_TYPE,
    game: GameState,
    session: dict[str, Any],
    token: str | None = None,
) -> None:
    token = await _prepare_operation(
        context, game, session, GamePhase.JUDGING, "verdict_generation", token
    )
    if token is None:
        return
    scenario = _scenario(game)
    try:
        verdict = await _with_model_timeout(
            scenario.generate_verdict(_snapshot(game, session))
        )
        if verdict is None:
            raise RuntimeError("model returned invalid verdict")
    except Exception:
        logger.exception("games: verdict generation failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Гейм-мастер не смог вынести корректный вердикт.",
            session=session,
            token=token,
            expected_phase=GamePhase.JUDGING,
        )
        return

    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.JUDGING):
            return
        session["content"]["verdict"] = verdict
        final_text = _verdict_text(game, session, verdict)

    try:
        final_message = await context.bot.send_message(
            chat_id=game.chat_id, text=final_text
        )
    except Exception:
        logger.exception("games: final message failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Не удалось опубликовать итоговый вердикт.",
            session=session,
            token=token,
            expected_phase=GamePhase.JUDGING,
        )
        return

    stale_message = False
    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.JUDGING):
            stale_message = True
        else:
            game.finish()
            _remove_game(context, game)
    if stale_message:
        await _delete_stale_message(context, game, final_message)


async def _create_lobby(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    game_type: str,
) -> None:
    if update.message is None or update.effective_user is None:
        return
    if not await ensure_master_in_chat_for_ai(update, context):
        return
    if game_type not in SCENARIOS:
        return

    remember_chat_user(context, update.effective_user)
    chat_id = update.effective_chat.id
    game = GameState(
        game_id=secrets.token_hex(4),
        game_type=game_type,
        chat_id=chat_id,
        creator_id=update.effective_user.id,
    )
    session = {
        "players": {
            update.effective_user.id: {
                "user_id": update.effective_user.id,
                "name": _display_name(update.effective_user),
            }
        },
        "content": {},
        "operation": None,
        "operation_token": None,
    }

    async with _lock(context):
        try:
            _store(context).add(game)
        except GameAlreadyActive:
            game_added = False
        else:
            game_added = True
            _sessions(context)[game.game_id] = session
    if not game_added:
        await update.message.reply_text(
            "В этом чате уже идёт групповая игра. Дождитесь завершения или /game_cancel."
        )
        return

    try:
        await update.message.reply_text(
            _lobby_text(game, session), reply_markup=_keyboard(game)
        )
    except Exception:
        logger.exception("games: lobby send failed game=%s", game.game_id)
        await _finish_without_winner(context, game, "Не удалось опубликовать лобби.")
        return

    schedule_failed = False
    async with _lock(context):
        if _is_current(context, game, GamePhase.LOBBY):
            schedule_failed = not _schedule_timeout(context, game, LOBBY_TIMEOUT_SECONDS)
    if schedule_failed:
        await _finish_without_winner(
            context, game, "Не удалось установить таймер лобби."
        )


@pause
async def alibi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _create_lobby(update, context, "alibi")


@pause
async def operation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _create_lobby(update, context, "operation")


@pause
async def pitch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _create_lobby(update, context, "pitch")


async def game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or update.effective_user is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 4 or parts[0] != "game":
        await query.answer("Некорректная команда игры.", show_alert=True)
        return

    _, game_type, game_id, action = parts
    user_id = update.effective_user.id
    async with _lock(context):
        game = _store(context).get(update.effective_chat.id)
        session = _sessions(context).get(game_id)
        error = None
        show_alert = True
        if game is None or game.game_id != game_id or game.game_type != game_type:
            error = "Эта игра уже завершена или потеряна после перезапуска."
        elif session is None:
            error = "Данные игры недоступны."
        elif game.phase is not GamePhase.LOBBY:
            error = (
                "Регистрация уже закрыта." if action == "join" else "Игра уже началась."
            )
        elif session.get("operation_token"):
            error = "Сейчас выполняется другое действие игры."
        elif action == "join" and user_id in game.participants:
            error = "Ты уже в списке участников."
            show_alert = False
        elif action == "join" and len(game.participants) >= game.MAX_PARTICIPANTS:
            error = "Лобби уже заполнено."
        elif action == "start" and user_id != game.creator_id:
            error = "Начать может только автор лобби."
        elif action == "start" and len(game.participants) < game.MIN_PARTICIPANTS:
            error = f"Нужно минимум {game.MIN_PARTICIPANTS} участника."
        elif action == "cancel" and user_id not in (
            game.creator_id,
            context.bot_data["master"],
        ):
            error = "Отменить может только автор или master."
        elif action not in ("join", "start", "cancel"):
            error = "Неизвестное действие."
    if error:
        await query.answer(error, show_alert=show_alert)
        return

    answer = {
        "join": "Ты в игре.",
        "start": "Гейм-мастер открывает дело.",
        "cancel": None,
    }[action]
    await query.answer(answer)

    async with _lock(context):
        if (
            not _is_current(context, game, GamePhase.LOBBY)
            or _sessions(context).get(game.game_id) is not session
            or session.get("operation_token")
        ):
            return
        token = _reserve_operation(session, f"lobby_{action}")
        if action == "join":
            try:
                game.join(user_id)
            except (GameFull, GameNotReady):
                _clear_operation(session, token)
                return
            session["players"][user_id] = {
                "user_id": user_id,
                "name": _display_name(update.effective_user),
            }
            edit_text = _lobby_text(game, session)
            edit_markup = _keyboard(game)
        elif action == "start":
            edit_text = f"{_scenario(game).title} начинается. Гейм-мастер думает..."
            edit_markup = None
        else:
            edit_text = f"{_scenario(game).title} отменена."
            edit_markup = None

    try:
        await query.edit_message_text(edit_text, reply_markup=edit_markup)
    except Exception:
        logger.exception("games: lobby edit failed game=%s", game.game_id)
        if action == "join":
            await _finish_without_winner(
                context,
                game,
                "Не удалось обновить состав лобби.",
                session=session,
                token=token,
                expected_phase=GamePhase.LOBBY,
            )
        else:
            async with _lock(context):
                if _operation_matches(context, game, session, token, GamePhase.LOBBY):
                    _clear_operation(session, token)
        return

    if action == "start":
        async with _lock(context):
            if not _operation_matches(context, game, session, token, GamePhase.LOBBY):
                return
            game.start(user_id)
            game.set_prompt_message(None)
            session["operation"] = "opening_generation"
        scheduled = _schedule_user_continuation(
            context,
            update,
            game,
            session,
            token,
            GamePhase.ROUND_ONE,
            "opening_generation",
            _start_round_one(context, game, session, token),
        )
        if not scheduled:
            await _finish_without_winner(
                context,
                game,
                "Не удалось запустить фоновую генерацию.",
                session=session,
                token=token,
                expected_phase=GamePhase.ROUND_ONE,
            )
        return

    async with _lock(context):
        if not _operation_matches(context, game, session, token, GamePhase.LOBBY):
            return
        if action == "cancel":
            _remove_game(context, game)
        else:
            _clear_operation(session, token)


async def process_game_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Consume a reply move for an active round. Returns True if handled."""
    message = update.message
    if (
        message is None
        or message.text is None
        or message.reply_to_message is None
        or update.effective_user is None
    ):
        return False

    response = None
    async with _lock(context):
        game = _store(context).get(update.effective_chat.id)
        if (
            game is None
            or game.phase not in (GamePhase.ROUND_ONE, GamePhase.ROUND_TWO)
            or message.reply_to_message.message_id != game.current_prompt_message_id
        ):
            return False
        session = _sessions(context).get(game.game_id)
        if session is None:
            return False
        user_id = update.effective_user.id
        if session.get("operation_token"):
            response = "Гейм-мастер ещё обрабатывает предыдущее действие."
        elif user_id not in game.participants:
            response = "Этот раунд только для зарегистрированных участников."
        else:
            submitted_phase = game.phase
            try:
                game.submit(user_id, message.text)
            except DuplicateSubmission:
                response = "Твой ход в этом раунде уже принят."
            except InvalidSubmission:
                response = (
                    f"Ответ должен содержать от 1 до {game.max_move_length} символов."
                )
            else:
                phase_after_submit = game.phase
                token = _reserve_operation(session, "submission_status")
                if phase_after_submit is not submitted_phase:
                    game.set_prompt_message(None)
                round_label = "первого" if submitted_phase is GamePhase.ROUND_ONE else "второго"
                submitted = game.submissions[submitted_phase]
                status_lines = "\n".join(
                    f"• {_player_name(session, participant_id)}: "
                    f"{_status(submitted, participant_id)}"
                    for participant_id in game.participants
                )
                status_text = _telegram_text(
                    f"Статус {round_label} раунда:\n{status_lines}"
                )
    if response is not None:
        await message.reply_text(response)
        return True

    try:
        await context.bot.send_message(chat_id=game.chat_id, text=status_text)
    except Exception:
        logger.exception("games: status send failed game=%s", game.game_id)
        await _finish_without_winner(
            context,
            game,
            "Не удалось обновить состояние раунда.",
            session=session,
            token=token,
            expected_phase=phase_after_submit,
        )
        return True

    transition = None
    async with _lock(context):
        if not _operation_matches(context, game, session, token, phase_after_submit):
            return True
        if phase_after_submit is submitted_phase:
            _clear_operation(session, token)
        elif phase_after_submit is GamePhase.ROUND_TWO:
            session["operation"] = "round_two_generation"
            transition = "round_two"
        else:
            session["operation"] = "verdict_generation"
            transition = "verdict"

    if transition == "round_two":
        scheduled = _schedule_user_continuation(
            context,
            update,
            game,
            session,
            token,
            GamePhase.ROUND_TWO,
            "round_two_generation",
            _start_round_two(context, game, session, token),
        )
    elif transition == "verdict":
        scheduled = _schedule_user_continuation(
            context,
            update,
            game,
            session,
            token,
            GamePhase.JUDGING,
            "verdict_generation",
            _publish_verdict(context, game, session, token),
        )
    else:
        scheduled = True
    if not scheduled:
        await _finish_without_winner(
            context,
            game,
            "Не удалось запустить фоновое продолжение игры.",
            session=session,
            token=token,
            expected_phase=phase_after_submit,
        )
    return True


async def llm_game_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reserve timeout transition under lock and generate outside it."""
    abort_reason = None
    transition = None
    session = None
    token = None
    async with _lock(context):
        data = context.job.data
        game = _store(context).get(data["chat_id"])
        if (
            game is None
            or game.game_id != data["game_id"]
            or game.phase.value != data["phase"]
            or game.timeout_job_name != data["job_name"]
        ):
            return
        if game.phase is GamePhase.LOBBY:
            _remove_game(context, game)
            abort_reason = "Время регистрации истекло."
        else:
            try:
                game.advance_on_timeout()
            except GameNotReady:
                _remove_game(context, game)
                abort_reason = "В раунде осталось меньше двух пригодных ответов."
            else:
                session = _sessions(context)[game.game_id]
                game.set_prompt_message(None)
                if game.phase is GamePhase.ROUND_TWO:
                    token = _reserve_operation(session, "round_two_generation")
                    transition = "round_two"
                else:
                    token = _reserve_operation(session, "verdict_generation")
                    transition = "verdict"
    if abort_reason:
        await _send_abort_message(context, game, abort_reason)
    elif transition == "round_two":
        await _start_round_two(context, game, session, token)
    elif transition == "verdict":
        await _publish_verdict(context, game, session, token)


async def game_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    async with _lock(context):
        game = _store(context).get(update.effective_chat.id)
        if game is None:
            response = "В этом чате нет активной групповой игры."
        else:
            user_id = update.effective_user.id
            master_id = context.bot_data["master"]
            allowed = (
                {game.creator_id, master_id}
                if game.phase is GamePhase.LOBBY
                else set(game.participants) | {master_id}
            )
            if user_id not in allowed:
                response = "Отменить игру могут только её участники или master."
            else:
                title = _scenario(game).title
                _remove_game(context, game)
                response = f"{title} отменена. Гейм-мастер закрывает дело."
    await update.message.reply_text(response)


async def _advance_after_round(
    context: ContextTypes.DEFAULT_TYPE, game_id: str
) -> None:
    """Compatibility shim for older tests; new flow advances from submit/timeout."""
    found = _find_by_id(context, game_id)
    if found is None:
        return
    game, session = found
    if game.phase is GamePhase.ROUND_TWO and "round_two" not in session["content"]:
        token = _reserve_operation(session, "round_two_generation")
        await _start_round_two(context, game, session, token)
    elif game.phase is GamePhase.JUDGING:
        token = _reserve_operation(session, "verdict_generation")
        await _publish_verdict(context, game, session, token)


game_round_timeout = llm_game_timeout
game_lobby_timeout = llm_game_timeout
