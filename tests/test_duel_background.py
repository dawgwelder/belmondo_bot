import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio

sys.modules.setdefault(
    "config",
    SimpleNamespace(client=Mock(), logger=Mock(), TELEGRAM_MAX_MESSAGE_LENGTH=4096),
)

import handlers.duel as duel_handlers
from spy_game.models import DuelWagerStatus
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings


SCENARIO = {"title": "Test", "setting": "Roof", "condition": "Win"}


class TaskApplication:
    def __init__(self):
        self.tasks = []

    def create_task(self, coroutine, **kwargs):
        task = asyncio.create_task(coroutine, name=kwargs.get("name"))
        self.tasks.append(task)
        return task

    async def wait_tasks(self):
        return await asyncio.wait_for(asyncio.gather(*self.tasks, return_exceptions=True), timeout=1)


def user(user_id):
    return SimpleNamespace(id=user_id, username=f"agent{user_id}", full_name=f"Private {user_id}", is_bot=False)


def command(user_id=1):
    return SimpleNamespace(
        effective_user=user(user_id),
        effective_chat=SimpleNamespace(id=-100),
        message=SimpleNamespace(
            reply_to_message=SimpleNamespace(from_user=user(2)),
            entities=(),
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        ),
    )


def callback(duel_id, user_id, action):
    return SimpleNamespace(
        effective_user=user(user_id),
        effective_chat=SimpleNamespace(id=-100),
        callback_query=SimpleNamespace(
            data=f"duel:{duel_id}:{action}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(message_id=900),
        ),
    )


@pytest_asyncio.fixture
async def context(tmp_path, monkeypatch):
    service = SpyGameService(
        SpySettings(
            mode="dev",
            enabled=True,
            database_path=tmp_path / "duels.sqlite3",
            allowed_chat_ids=frozenset({-100}),
        )
    )
    await service.initialize()
    await service.enable_chat(-100)
    for user_id in (1, 2):
        await service.get_profile(user_id=user_id, username=f"agent{user_id}", display_name=f"Private {user_id}")
        await service.database.transaction(
            lambda connection, uid=user_id: connection.execute(
                "INSERT INTO user_agents(user_id, agent_type, amount) VALUES (?, 'informant', 10)",
                (uid,),
            ),
            immediate=True,
        )
    ctx = SimpleNamespace(
        args=[],
        chat_data={},
        bot_data={"spy_game": service, "master": 999},
        bot=SimpleNamespace(delete_message=AsyncMock()),
        job_queue=SimpleNamespace(run_once=Mock(), get_jobs_by_name=lambda _: ()),
        application=TaskApplication(),
    )
    monkeypatch.setattr(duel_handlers, "ensure_master_in_chat_for_ai", AsyncMock(return_value=True))
    monkeypatch.setattr(duel_handlers, "_generate_scenario", AsyncMock(return_value=SCENARIO))
    yield ctx
    for task in ctx.application.tasks:
        if not task.done():
            task.cancel()
    await ctx.application.wait_tasks()
    await service.close()


@pytest.mark.asyncio
async def test_preparation_returns_immediately_reserves_chat_and_can_be_cancelled(context, monkeypatch):
    started = asyncio.Event()

    async def slow_scenario(*_args):
        started.set()
        await asyncio.Event().wait()

    generator = AsyncMock(side_effect=slow_scenario)
    monkeypatch.setattr(duel_handlers, "_generate_scenario", generator)
    first = command()
    await asyncio.wait_for(duel_handlers.duel(first, context), timeout=1)
    await asyncio.wait_for(started.wait(), timeout=1)
    assert next(iter(context.chat_data["duels"].values()))["status"] == "preparing"
    first.message.reply_text.assert_not_awaited()

    duplicate = command()
    await duel_handlers.duel(duplicate, context)
    assert "уже идёт" in duplicate.message.reply_text.await_args.args[0]
    generator.assert_awaited_once()

    await duel_handlers.duel_cancel(command(), context)
    results = await context.application.wait_tasks()
    assert isinstance(results[0], asyncio.CancelledError)
    assert context.chat_data["duels"] == {}
    assert (await context.bot_data["spy_game"].get_active_duel_wager(-100)).status is DuelWagerStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_cancel_after_escrow_refunds_before_background_exits(context, monkeypatch):
    service = context.bot_data["spy_game"]
    create = service.create_duel_wager
    debited = asyncio.Event()
    created = []

    async def slow_create(**kwargs):
        result = await create(**kwargs)
        created.append(result.duel_id)
        debited.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "create_duel_wager", slow_create)
    first = command()
    await duel_handlers.duel(first, context)
    await asyncio.wait_for(debited.wait(), timeout=1)
    await duel_handlers.duel_cancel(command(), context)
    await context.application.wait_tasks()

    assert (await service.get_duel_wager(created[0])).status is DuelWagerStatus.REFUNDED
    assert next(agent.amount for agent in await service.get_agents(1) if agent.agent_type == "informant") == 10
    assert context.chat_data["duels"] == {}
    first.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_cancelled_publication_is_deleted_and_refunded(context):
    sending = asyncio.Event()

    async def late_reply(*_args, **_kwargs):
        sending.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return SimpleNamespace(message_id=900)

    first = command()
    first.message.reply_text.side_effect = late_reply
    await duel_handlers.duel(first, context)
    await asyncio.wait_for(sending.wait(), timeout=1)
    duel_id = next(iter(context.chat_data["duels"]))
    await duel_handlers.duel_cancel(command(), context)
    await context.application.wait_tasks()

    assert (await context.bot_data["spy_game"].get_duel_wager(duel_id)).status is DuelWagerStatus.REFUNDED
    context.bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=900)
    context.job_queue.run_once.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_at", ["message", "timer"])
async def test_failed_publication_refunds_and_releases_chat(context, failure_at):
    update = command()
    failing_call = update.message.reply_text if failure_at == "message" else context.job_queue.run_once
    failing_call.side_effect = RuntimeError("publication failed")
    await duel_handlers.duel(update, context)
    duel_id = next(iter(context.chat_data["duels"]))
    results = await context.application.wait_tasks()
    assert isinstance(results[0], RuntimeError)
    assert context.chat_data["duels"] == {}
    assert (await context.bot_data["spy_game"].get_duel_wager(duel_id)).status is DuelWagerStatus.REFUNDED
    if failure_at == "timer":
        context.bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=900)


@pytest.mark.asyncio
async def test_narration_does_not_block_or_resettle_and_cannot_be_cancelled(context, monkeypatch):
    await duel_handlers.duel(command(), context)
    assert await context.application.wait_tasks() == [None]
    duel_id = next(iter(context.chat_data["duels"]))
    await duel_handlers.duel_callback(callback(duel_id, 2, "accept"), context)
    await duel_handlers.duel_callback(callback(duel_id, 1, "move:defend"), context)
    narrating, release = asyncio.Event(), asyncio.Event()

    async def narrate(_duel, winner_key):
        narrating.set()
        await release.wait()
        return {"winner": winner_key, "narration": "Done", "reason": "Server result"}

    narrator = AsyncMock(side_effect=narrate)
    monkeypatch.setattr(duel_handlers, "_narrate_wagered_duel", narrator)
    last = callback(duel_id, 2, "move:attack")
    await asyncio.wait_for(duel_handlers.duel_callback(last, context), timeout=1)
    await asyncio.wait_for(narrating.wait(), timeout=1)
    persisted = await context.bot_data["spy_game"].get_duel_wager(duel_id)
    assert persisted.status is DuelWagerStatus.WON
    assert persisted.winner_user_id == 1
    assert context.chat_data["duels"][duel_id]["status"] == "judging"

    await duel_handlers.duel_callback(callback(duel_id, 2, "move:attack"), context)
    cancellation = command()
    await duel_handlers.duel_cancel(cancellation, context)
    assert "Результат дуэли уже определён" in cancellation.message.reply_text.await_args.args[0]
    narrator.assert_awaited_once()
    release.set()
    assert await context.application.wait_tasks() == [None, None]
    assert context.chat_data["duels"] == {}
    assert "Победитель: @agent1" in last.callback_query.edit_message_text.await_args.args[0]
    assert (await context.bot_data["spy_game"].get_duel_wager(duel_id)).winner_user_id == 1


@pytest.mark.asyncio
async def test_stale_narration_cannot_publish_or_remove_replacement(context, monkeypatch):
    old = {
        "id": "old",
        "status": "judging",
        "players": {
            "challenger": {"id": 1, "name": "one"},
            "opponent": {"id": 2, "name": "two"},
        },
        "scenario": SCENARIO,
        "actions": {"1": "defend", "2": "attack"},
    }
    context.chat_data["duels"] = {"old": old}
    replacement = dict(old)

    async def stale_result(*_args):
        context.chat_data["duels"]["old"] = replacement
        return {"winner": "challenger", "narration": "Old", "reason": "Old"}

    monkeypatch.setattr(duel_handlers, "_narrate_wagered_duel", stale_result)
    update = callback("old", 2, "move:attack")
    await duel_handlers._publish_duel_verdict(update, context, old, "challenger")
    assert context.chat_data["duels"]["old"] is replacement
    update.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["connect", "stream"])
@pytest.mark.parametrize("operation", ["scenario", "narration", "judgement"])
async def test_duel_model_timeout_covers_connection_and_stream(monkeypatch, phase, operation):
    closed = asyncio.Event()

    class Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            closed.set()

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    async def create(**_kwargs):
        if phase == "connect":
            await asyncio.Event().wait()
        return Stream()

    monkeypatch.setattr(duel_handlers, "DUEL_MODEL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        duel_handlers, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    )
    if operation == "scenario":
        pending = duel_handlers._generate_scenario("one", "two")
    else:
        duel = {
            "players": {
                "challenger": {"id": 1, "name": "one"},
                "opponent": {"id": 2, "name": "two"},
            },
            "scenario": SCENARIO,
            "actions": {"1": "defend", "2": "attack"},
        }
        pending = (
            duel_handlers._narrate_wagered_duel(duel, "opponent")
            if operation == "narration"
            else duel_handlers._judge_duel(duel)
        )
    result = await asyncio.wait_for(pending, timeout=1)
    if operation == "scenario":
        assert result in duel_handlers._FALLBACK_SCENARIOS
    else:
        assert result["winner"] == ("opponent" if operation == "narration" else "challenger")
    assert closed.is_set() is (phase == "stream")
