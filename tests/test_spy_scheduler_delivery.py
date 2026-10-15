from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from telegram.error import BadRequest, NetworkError, RetryAfter

from handlers.spy import chase as adapter
from spy_game.service import SpyGameService
from test_spy_chase import chase, press
from test_spy_game import NOW, initialized_service


@pytest_asyncio.fixture
async def pending_chase(tmp_path, monkeypatch):
    service = await initialized_service(tmp_path)
    clock = [NOW + timedelta(seconds=30)]
    monkeypatch.setattr("spy_game.use_cases.events.moscow_now", lambda: clock[0])
    monkeypatch.setattr(adapter, "moscow_now", lambda: clock[0])
    event_id = await chase(service)
    await press(service, event_id)
    await service.settle_chases(now=clock[0])
    context = SimpleNamespace(
        bot=SimpleNamespace(edit_message_text=AsyncMock(), send_message=AsyncMock()),
        bot_data={"spy_game": service},
    )
    try:
        yield service, event_id, context, clock
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["Message to edit not found", "Message can't be edited"])
async def test_chase_missing_message_fallback_is_delivered_once(pending_chase, error):
    service, event_id, context, _ = pending_chase
    context.bot.edit_message_text.side_effect = BadRequest(error)
    result = await service.get_chase(event_id)

    await adapter.render_chase(context, service, result)
    await adapter.render_chase(context, service, result)
    await adapter.chase_tick(context)

    context.bot.edit_message_text.assert_awaited_once()
    context.bot.send_message.assert_awaited_once_with(chat_id=result.chat_id, text=adapter.chase_text(result))
    assert await service.settle_chases() == ()
    assert (await service.get_agents(1))[0].amount == 1


@pytest.mark.asyncio
async def test_chase_failed_fallback_retries_after_restart(pending_chase):
    service, event_id, context, clock = pending_chase
    context.bot.edit_message_text.side_effect = BadRequest("Message to edit not found")
    context.bot.send_message.side_effect = [NetworkError("offline"), None]
    await adapter.chase_tick(context)
    assert (
        await service.database.read(
            lambda c: c.execute("SELECT notified_at FROM chase_rounds WHERE event_id=?", (event_id,)).fetchone()[0]
        )
        is None
    )
    await service.close()

    restarted = SpyGameService(service.settings)
    try:
        await restarted.initialize(now=clock[0])
        context.bot_data["spy_game"] = restarted
        clock[0] += timedelta(seconds=59)
        await adapter.chase_tick(context)
        context.bot.send_message.assert_awaited_once()
        clock[0] += timedelta(seconds=1)
        await adapter.chase_tick(context)
        assert context.bot.send_message.await_count == 2
        assert await restarted.settle_chases() == ()
        assert (await restarted.get_agents(1))[0].amount == 1
    finally:
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "delay"),
    [
        (NetworkError("offline"), 60),
        (RetryAfter(180), 180),
        (RetryAfter(timedelta(seconds=180)), 180),
    ],
)
async def test_chase_transient_failure_delays_callback_and_timer_retry(pending_chase, error, delay):
    service, event_id, context, clock = pending_chase
    context.bot.edit_message_text.side_effect = [error, None]
    result = await service.get_chase(event_id)
    with pytest.raises(type(error)):
        await adapter.render_chase(context, service, result)

    clock[0] += timedelta(seconds=delay - 1)
    await adapter.render_chase(context, service, result)
    await adapter.chase_tick(context)
    context.bot.edit_message_text.assert_awaited_once()
    context.bot.send_message.assert_not_awaited()
    clock[0] += timedelta(seconds=1)
    await adapter.chase_tick(context)
    assert context.bot.edit_message_text.await_count == 2
    assert await service.settle_chases() == ()


@pytest.mark.asyncio
async def test_chase_failed_delivery_does_not_block_other_results(pending_chase):
    service, _, context, clock = pending_chase
    other = await chase(service)
    await service.attach_message(other, 43)
    await press(service, other)
    await service.settle_chases(now=clock[0])
    context.bot.edit_message_text.side_effect = [NetworkError("offline"), None]

    await adapter.chase_tick(context)

    assert context.bot.edit_message_text.await_count == 2
    rows = await service.database.read(lambda c: c.execute("SELECT notified_at FROM chase_rounds").fetchall())
    assert sum(row[0] is not None for row in rows) == 1
    assert await service.settle_chases() == ()


@pytest.mark.asyncio
async def test_chase_delivery_batch_is_bounded(pending_chase):
    service, _, context, clock = pending_chase
    for _ in range(20):
        event_id = await chase(service)
        await press(service, event_id)
        await service.settle_chases(now=clock[0])

    await adapter.chase_tick(context)
    assert context.bot.edit_message_text.await_count == 20
    await adapter.chase_tick(context)
    assert context.bot.edit_message_text.await_count == 21
    assert await service.settle_chases() == ()


@pytest.mark.asyncio
async def test_disabled_app_schedules_cleanup_for_unexpired_duel(tmp_path, monkeypatch):
    import app
    from test_spy_duels import NOW as DUEL_NOW, balance, create_wager, service_with_agents

    previous = await service_with_agents(tmp_path)
    duel = await create_wager(previous)
    config = replace(previous.settings, enabled=False)
    await previous.close()
    clock = [DUEL_NOW + timedelta(seconds=1)]
    monkeypatch.setattr("spy_game.service.moscow_now", lambda: clock[0])
    monkeypatch.setattr(app.SpySettings, "from_env", lambda mode: config)
    monkeypatch.setattr(app.SpyWebAppSettings, "from_env", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr(app, "build_narrator", Mock())
    monkeypatch.setattr(app, "load_tarot_deck", lambda: [])
    monkeypatch.setattr(app, "close_horoscope_http_client", AsyncMock())
    application = SimpleNamespace(
        bot_data={},
        bot=Mock(),
        job_queue=Mock(),
        add_handler=Mock(),
        initialize=AsyncMock(),
        start=AsyncMock(),
        stop=AsyncMock(),
        shutdown=AsyncMock(),
    )

    async def start_polling(**kwargs):
        service = application.bot_data["spy_game"]
        assert await balance(service, 1) == 7
        jobs = {call.kwargs["name"]: call.args[0] for call in application.job_queue.run_repeating.call_args_list}
        assert "spy-chase-tick" not in jobs
        clock[0] = duel.expires_at + timedelta(seconds=1)
        await jobs["spy-game-tick"](SimpleNamespace(bot=application.bot, bot_data=application.bot_data))
        assert await balance(service, 1) == 10
        assert (
            await service.database.read(
                lambda c: c.execute("SELECT status FROM spy_duels WHERE id=?", (duel.duel_id,)).fetchone()[0]
            )
            == "refunded"
        )
        assert await service.database.read(lambda c: c.execute("SELECT COUNT(*) FROM game_events").fetchone()[0]) == 0
        raise KeyboardInterrupt

    application.updater = SimpleNamespace(start_polling=start_polling, stop=AsyncMock())

    class Builder:
        def build(self):
            return application

        def __getattr__(self, name):
            return lambda *args, **kwargs: self

    monkeypatch.setattr(app, "Application", SimpleNamespace(builder=Builder))
    await app.main(token="test")
