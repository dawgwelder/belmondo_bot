"""Timed deletion of temporary Spy menu responses in group chats."""
from __future__ import annotations

from datetime import timedelta

from telegram.error import BadRequest, NetworkError, RetryAfter

from config import logger

MENU_MESSAGE_TTL_SECONDS = 5 * 60
MAX_DELETE_RETRIES = 2


def schedule_menu_cleanup(context, chat, message_id: int) -> None:
    if getattr(chat, "type", None) not in {"group", "supergroup"}:
        return
    queue = getattr(context, "job_queue", None)
    if queue is None:
        logger.warning("spy_game: menu cleanup requires JobQueue chat_id=%s", chat.id)
        return
    name = f"spy-menu-cleanup:{chat.id}:{message_id}"
    for job in queue.get_jobs_by_name(name):
        job.schedule_removal()
    queue.run_once(
        delete_menu_message,
        MENU_MESSAGE_TTL_SECONDS,
        data={"chat_id": chat.id, "message_id": message_id, "retries": 0},
        name=name,
        chat_id=chat.id,
    )


async def delete_menu_message(context) -> None:
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"], message_id=data["message_id"]
        )
        return
    except RetryAfter as error:
        delay = error.retry_after
        if isinstance(delay, timedelta):
            delay = delay.total_seconds()
        delay = max(1, delay)
    except NetworkError as error:
        # BadRequest derives from NetworkError in python-telegram-bot.
        if isinstance(error, BadRequest):
            if "message to delete not found" not in str(error).lower():
                logger.warning(
                    "spy_game: menu message cannot be deleted chat_id=%s message_id=%s",
                    data["chat_id"],
                    data["message_id"],
                )
            return
        delay = 30
    except Exception:
        logger.warning(
            "spy_game: menu deletion failed chat_id=%s message_id=%s",
            data["chat_id"],
            data["message_id"],
        )
        return
    if data["retries"] < MAX_DELETE_RETRIES:
        context.job_queue.run_once(
            delete_menu_message,
            delay,
            data={**data, "retries": data["retries"] + 1},
            name=context.job.name,
            chat_id=data["chat_id"],
        )
    else:
        logger.warning(
            "spy_game: menu deletion retries exhausted chat_id=%s message_id=%s",
            data["chat_id"],
            data["message_id"],
        )
