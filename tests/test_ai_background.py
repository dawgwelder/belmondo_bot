import asyncio
import sys
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import Application, TypeHandler

from time_utils import MOSCOW

sys.modules.setdefault(
    "config",
    SimpleNamespace(client=Mock(), logger=Mock(), TELEGRAM_MAX_MESSAGE_LENGTH=4096, tz=MOSCOW),
)

from handlers import ai
from handlers.tldr import tldr
from state import ensure_chat_state


class Tasks:
    def __init__(self):
        self.tasks = []

    def create_task(self, coroutine, *, update, name):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task


def request(chat_id=1):
    user = SimpleNamespace(id=123456789)
    update = SimpleNamespace(
        message=SimpleNamespace(
            text="hello",
            message_id=10,
            from_user=user,
            reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=99)),
        ),
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=user,
        callback_query=None,
    )
    context = SimpleNamespace(
        application=Tasks(),
        chat_data={},
        bot_data={"self_id": 99},
        args=[],
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    return update, context


@pytest.fixture
def model(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(ai, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(ai, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True))
    monkeypatch.setattr(ai, "parse_stream", AsyncMock(return_value="answer"))
    return create


@pytest.mark.asyncio
async def test_chat_reply_returns_before_model_and_reserves_only_one_request(model):
    update, context = request()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await release.wait()

    model.side_effect = blocked
    await ai.process_ai_response(update, context)
    await asyncio.wait_for(entered.wait(), 1)
    active = context.chat_data["ai_task"]
    await ai.process_ai_response(update, context)
    assert context.chat_data["ai_task"] is active
    assert len(context.application.tasks) == 1
    context.bot.send_message.assert_not_awaited()
    release.set()
    await active
    assert "ai_task" not in context.chat_data
    assert not context.chat_data["ai_lock"].locked()
    context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_normal_message_does_not_create_background_task(model):
    update, context = request()
    update.message.reply_to_message = None
    await ai.process_ai_response(update, context)
    assert not context.application.tasks
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_history_always_has_system_prompt(model):
    update, context = request()
    ensure_chat_state(context)["chat_deque"] = deque(
        ({"role": role, "content": str(i)} for i in range(50) for role in ("user", "assistant")),
        maxlen=100,
    )
    for _ in range(3):
        await ai.process_ai_response(update, context)
        await context.application.tasks[-1]
        messages = model.call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": ai.const.professional_prompt}
        assert sum(message["role"] == "system" for message in messages) == 1
        assert messages[-1] == {"role": "user", "content": "hello"}
        assert len(messages) == 101
        assert all(message["role"] != "system" for message in context.chat_data["chat_deque"])


@pytest.mark.asyncio
@pytest.mark.parametrize("during_stream", [False, True])
async def test_timeout_releases_chat_and_cancels_model(model, monkeypatch, during_stream):
    update, context = request()
    cancelled = asyncio.Event()

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(ai, "_AI_TIMEOUT_SECONDS", 0.01)
    if during_stream:
        monkeypatch.setattr(ai, "parse_stream", blocked)
    else:
        model.side_effect = blocked
    await ai.process_ai_response(update, context)
    await asyncio.wait_for(context.application.tasks[-1], 1)
    assert cancelled.is_set()
    assert "ai_task" not in context.chat_data
    assert not context.chat_data["ai_lock"].locked()
    assert not any(message["role"] == "assistant" for message in context.chat_data["chat_deque"])
    assert "слишком много времени" in context.bot.send_message.call_args.kwargs["text"]


@pytest.mark.asyncio
async def test_clear_context_cancels_pending_reply_before_reset(model):
    update, context = request()
    entered = asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    model.side_effect = blocked
    await ai.process_ai_response(update, context)
    await entered.wait()
    task = context.chat_data["ai_task"]
    await ai.clear_context(update, context)
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert not context.chat_data["chat_deque"]
    assert "ai_task" not in context.chat_data
    assert not context.chat_data["ai_lock"].locked()
    context.bot.send_message.assert_awaited_once_with(chat_id=1, text="Контекст очищен")


@pytest.mark.asyncio
async def test_placeholder_cleanup_cancels_pending_animation_edit(monkeypatch):
    update, context = request()
    editing, stopped = asyncio.Event(), asyncio.Event()

    async def animation(*args, **kwargs):
        editing.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(ai, "_send_placeholder", AsyncMock())
    monkeypatch.setattr(ai, "_cycle_placeholder_text", animation)

    async def complete():
        async with ai._animated_placeholder(update, context, ["working"], log_prefix="test"):
            await editing.wait()

    await asyncio.wait_for(complete(), 1)
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_cancel_during_placeholder_cleanup_cannot_send_late_reply(monkeypatch):
    update, context = request()
    editing, cleaning = asyncio.Event(), asyncio.Event()

    async def animation(*args, **kwargs):
        editing.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(ai, "_send_placeholder", AsyncMock())
    monkeypatch.setattr(ai, "_cycle_placeholder_text", animation)

    async def reply():
        async with ai._animated_placeholder(update, context, ["working"], log_prefix="test"):
            await editing.wait()
        await context.bot.send_message(text="late reply")

    task = asyncio.create_task(reply())
    await asyncio.wait_for(cleaning.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_context_does_not_wait_for_cleanup_or_allow_overlapping_ai(model):
    update, context = request()
    entered, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release_cleanup.wait()

    model.side_effect = blocked
    await ai.process_ai_response(update, context)
    await entered.wait()
    task = context.chat_data["ai_task"]
    try:
        await asyncio.wait_for(ai.clear_context(update, context), 1)
        await cleaning.wait()
        assert not task.done()
        assert not context.chat_data["chat_deque"]
        await ai.process_ai_response(update, context)
        assert len(context.application.tasks) == 1
    finally:
        release_cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
    assert "ai_task" not in context.chat_data
    assert not context.chat_data["chat_deque"]
    context.bot.send_message.assert_awaited_once_with(chat_id=1, text="Контекст очищен")


@pytest.mark.asyncio
async def test_cancel_before_ptb_starts_task_closes_managed_coroutine(model, monkeypatch):
    update, context = request()
    application = Application.builder().token("123:test").updater(None).job_queue(None).build()
    application._running = True
    context.application = application
    coroutines = []
    original = Application.create_task

    def track(self, coroutine, **kwargs):
        coroutines.append(coroutine)
        return original(self, coroutine, **kwargs)

    monkeypatch.setattr(Application, "create_task", track)
    try:
        await ai.process_ai_response(update, context)
        task = context.chat_data["ai_task"]
        await ai.clear_context(update, context)
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()
        assert coroutines[0].cr_frame is None
        model.assert_not_awaited()
    finally:
        application._running = False


@pytest.mark.asyncio
@pytest.mark.parametrize("handler", [ai.ai_horoscope, ai.tarot, ai.magic_prediction_callback, tldr])
async def test_commands_share_busy_chat_reservation(handler):
    update, context = request()
    task = asyncio.create_task(asyncio.Event().wait())
    context.chat_data["ai_task"] = task
    if handler is ai.magic_prediction_callback:
        update.callback_query = SimpleNamespace(answer=AsyncMock())
    try:
        await handler(update, context)
        assert not context.application.tasks
        assert context.chat_data["ai_task"] is task
        if update.callback_query:
            update.callback_query.answer.assert_awaited_once()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_sequential_telegram_queue_handles_next_update_while_model_waits(model, monkeypatch):
    application = Application.builder().token("123:test").updater(None).job_queue(None).build()
    application.bot_data["self_id"] = 99
    application._initialized = True
    application._running = True
    sent = AsyncMock()
    monkeypatch.setattr(type(application.bot), "send_message", sent)
    entered, release, second_done = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(**kwargs):
        entered.set()
        await release.wait()

    async def dispatch(update, context):
        if update.update_id == 1:
            await ai.process_ai_response(update, context)
        else:
            second_done.set()

    model.side_effect = blocked
    application.add_handler(TypeHandler(Update, dispatch))
    message = Message(
        10,
        datetime.now(timezone.utc),
        Chat(1, "group"),
        from_user=User(123456789, "user", False),
        text="hello",
        reply_to_message=Message(9, datetime.now(timezone.utc), Chat(1, "group"), from_user=User(99, "bot", True)),
    )
    fetcher = asyncio.create_task(application._update_fetcher())
    try:
        await application.update_queue.put(Update(1, message=message))
        await asyncio.wait_for(entered.wait(), 1)
        await application.update_queue.put(Update(2, message=message))
        await asyncio.wait_for(second_done.wait(), 1)
        assert application.update_processor.max_concurrent_updates == 1
        assert not release.is_set()
        active = application.chat_data[1]["ai_task"]
        release.set()
        await asyncio.wait_for(active, 1)
    finally:
        release.set()
        fetcher.cancel()
        await asyncio.gather(fetcher, return_exceptions=True)
        if active := application.chat_data.get(1, {}).get("ai_task"):
            await asyncio.gather(active, return_exceptions=True)
        application._running = False
