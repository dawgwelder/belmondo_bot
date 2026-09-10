"""Temporary menu responses expire without deleting shared game messages."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter

from handlers.spy import achievements, cleanup, transport
from handlers.spy.callbacks import spy_callback
from handlers.spy.menu import spy_menu
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings


def context_for_cleanup():
    return SimpleNamespace(
        bot=SimpleNamespace(
            token="TOKEN",
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=88)),
            delete_message=AsyncMock(),
        ),
        bot_data={},
        job_queue=SimpleNamespace(
            get_jobs_by_name=Mock(return_value=()), run_once=Mock()
        ),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "supergroup", "private"])
@pytest.mark.parametrize("fallback", [False, True])
async def test_menu_cleanup_covers_rich_and_plain_group_messages(
    monkeypatch, chat_type, fallback
):
    context = context_for_cleanup()
    send = AsyncMock(return_value={"result": {"message_id": 77}})
    if fallback:
        send.side_effect = RuntimeError("rich unavailable")
    monkeypatch.setattr(transport, "send_rich_message", send)
    chat = SimpleNamespace(id=-100, type=chat_type)
    message_id = await transport._send_temporary_rich(
        context, chat, [], fallback_text="Досье"
    )
    assert message_id == (88 if fallback else 77)
    if chat_type == "private":
        context.job_queue.run_once.assert_not_called()
    else:
        job = context.job_queue.run_once.call_args
        assert job.args == (cleanup.delete_menu_message, 300)
        assert job.kwargs["data"] == {
            "chat_id": -100,
            "message_id": message_id,
            "retries": 0,
        }
    context.job_queue.run_once.reset_mock()
    await transport._send_rich(context, chat.id, [], fallback_text="Событие")
    context.job_queue.run_once.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "screen",
    [
        "command",
        "refresh",
        "profile",
        "agents",
        "inventory",
        "contacts",
        "leaderboard",
        "status",
        "achievements",
        "agency",
    ],
)
async def test_spy_screens_schedule_their_own_response(tmp_path, monkeypatch, screen):
    context = context_for_cleanup()
    service = SpyGameService(
        SpySettings(
            mode="dev",
            enabled=True,
            database_path=tmp_path / "spy.sqlite3",
            allowed_chat_ids=frozenset({-100}),
        )
    )
    await service.initialize()
    await service.enable_chat(-100)
    context.bot_data["spy_game"] = service
    monkeypatch.setattr(
        transport,
        "send_rich_message",
        AsyncMock(return_value={"result": {"message_id": 77}}),
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_user=SimpleNamespace(
            id=1, username="bond", full_name="Private Name", is_bot=False
        ),
        callback_query=SimpleNamespace(data=f"spy:menu:{screen}", answer=AsyncMock()),
        message=SimpleNamespace(message_id=11),
    )
    try:
        if screen == "command":
            await spy_menu(update, context)
        else:
            if screen == "agency":
                update.callback_query.data = "spy:agency:status"
            await spy_callback(update, context)
        context.job_queue.run_once.assert_called_once()
        job = context.job_queue.run_once.call_args
        expected_id = 88 if screen == "achievements" else 77
        assert job.kwargs["data"]["message_id"] == expected_id
        assert job.kwargs["data"]["chat_id"] == -100
        if screen == "achievements":
            old_job = Mock()
            context.job_queue.get_jobs_by_name.return_value = (old_job,)
            update.callback_query.message = SimpleNamespace(
                message_id=88, text="old page", reply_markup=None
            )
            update.callback_query.edit_message_text = AsyncMock()
            await achievements.send_archive(update, context, page=1, edit=True)
            old_job.schedule_removal.assert_called_once()
            assert context.job_queue.run_once.call_args.args[1] == 300
            assert (
                context.job_queue.run_once.call_args.kwargs["data"]["message_id"] == 88
            )
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,delay",
    [
        (None, None),
        (BadRequest("Message to delete not found"), None),
        (RetryAfter(12), 12),
        (NetworkError("offline"), 30),
    ],
)
async def test_deletion_handles_missing_messages_and_retries_transient_errors(
    error, delay
):
    context = context_for_cleanup()
    context.job = SimpleNamespace(
        data={"chat_id": -100, "message_id": 77, "retries": 0},
        name="spy-menu-cleanup:-100:77",
    )
    context.bot.delete_message.side_effect = error
    await cleanup.delete_menu_message(context)
    context.bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=77)
    if delay is None:
        context.job_queue.run_once.assert_not_called()
    else:
        assert context.job_queue.run_once.call_args.args[1] == delay
        assert context.job_queue.run_once.call_args.kwargs["data"]["retries"] == 1
        context.job_queue.run_once.reset_mock()
        context.job.data["retries"] = cleanup.MAX_DELETE_RETRIES
        await cleanup.delete_menu_message(context)
        context.job_queue.run_once.assert_not_called()
