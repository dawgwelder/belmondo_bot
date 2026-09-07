"""Spy Clicker Telegram missions."""
from __future__ import annotations

from telegram import CallbackGame, InlineKeyboardButton, InlineKeyboardMarkup
from config import logger
from telegram.error import BadRequest
from spy_game import death_mission_ui
from .context import _service
from .formatting import _display_name


async def _death_mission_callback(update, context, category, value):
    service = _service(context)
    async with death_mission_ui.delivery_lock(service):
        await _death_mission_callback_locked(update, context, category, value)
    await death_mission_ui.publish_pending(service, context.bot)


async def _death_mission_callback_locked(update, context, category, value):
    query, user, chat = (
        update.callback_query,
        update.effective_user,
        update.effective_chat,
    )
    service = _service(context)
    if query.message is None:
        await query.answer("Откройте сообщение операции в группе.", show_alert=True)
        return
    message_id = query.message.message_id
    code = None
    try:
        if category == "deathmenu":
            event_matches = await service.database.read(
                lambda connection: connection.execute(
                    "SELECT 1 FROM game_events WHERE id=? AND chat_id=? AND message_id=?",
                    (value, chat.id, message_id),
                ).fetchone()
                is not None
            )
            if not event_matches:
                await query.answer("Это сообщение другой операции.", show_alert=True)
                return
            result = await service.start_death_mission(
                chat_id=chat.id,
                message_id=message_id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
            if not result.launch_token:
                await query.answer(
                    "Операция уже занята или недоступна.", show_alert=True
                )
                return
            run_id = await service.death_mission_run_id(result.launch_token)
        else:
            run_id, revision, code = value.split(".")
            if code not in {"askextract", "askabandon"}:
                action, choice = death_mission_ui.decode(code)
                result = await service.mission_callback(
                    run_id=run_id,
                    user_id=user.id,
                    chat_id=chat.id,
                    message_id=message_id,
                    action=action,
                    revision=int(revision),
                    operation_id=query.id,
                    choice=choice,
                )
                if result.status == "forbidden":
                    await query.answer("Это операция другого агента.", show_alert=True)
                    return
                if result.payload.get("error"):
                    await query.answer(
                        death_mission_ui.ERRORS.get(
                            result.payload["error"], "Действие недоступно."
                        ),
                        show_alert=True,
                    )
                else:
                    await query.answer()

        # Read the latest revision, including after an idempotent retry.
        def current(connection):
            row = service.repository.death_mission.row(connection, run_id=run_id)
            if not row or (row["user_id"], row["chat_id"], row["message_id"]) != (
                user.id,
                chat.id,
                message_id,
            ):
                return None
            from spy_game.service import utc_now

            row = service.repository.death_mission.refresh(connection, row, utc_now())
            return (
                service.repository.death_mission.view(connection, row).payload,
                row["event_id"],
            )

        latest = await service.database.transaction(current, immediate=True)
        if latest is None:
            await query.answer("Это операция другого агента.", show_alert=True)
            return
        payload, event_id = latest
        if category == "deathmenu" or code in {"askextract", "askabandon"}:
            await query.answer()
        if payload["status"] in death_mission_ui.TERMINAL:
            return
        markup = death_mission_ui.keyboard(payload, run_id, event_id)
        copy = death_mission_ui.text(payload)
        if code in {"askextract", "askabandon"} and payload["status"] == "in_run":
            extract = code == "askextract"
            if extract and not payload["mission"]["checkpoint"]:
                return
            action = "extract" if extract else "abandon"
            copy += "\n\n" + (
                "Завершить миссию и вернуть указанный состав?"
                if extract
                else "Сдаться и потерять всю ставку?"
            )
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Подтвердить",
                            callback_data=f"spy:mission:{run_id}.{payload['revision']}.{action}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Продолжить миссию",
                            callback_data=f"spy:deathmenu:{event_id}",
                        )
                    ],
                ]
            )
        if getattr(query.message, "game", None) is not None:
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Открыть HTML5", callback_game=CallbackGame()
                        )
                    ],
                    *(markup.inline_keyboard if markup else []),
                ]
            )
        try:
            await query.edit_message_text(text=copy, reply_markup=markup)
        except BadRequest as error:
            if "message is not modified" not in str(error).lower():
                raise
    except (ValueError, TypeError):
        await query.answer("Некорректное действие операции.", show_alert=True)
    except Exception:
        logger.exception("death mission callback failed")
        await query.answer(
            "Не удалось получить ответ. Откройте операцию повторно: состояние сохранено на сервере.",
            show_alert=True,
        )
