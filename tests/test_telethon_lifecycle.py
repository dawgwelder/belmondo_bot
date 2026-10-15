import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import telethon_utils
from godnoscop import godnoscop_tracker
from tldr import history


@pytest.fixture
def telethon_stub(tmp_path, monkeypatch):
    config = {
        "auth": {"phone": "test", "api_id": 1, "api_hash": "test"},
        "paths": {"gonoscopes_path": str(tmp_path / "horoscopes.json")},
    }
    client = SimpleNamespace(connected=False)

    async def start():
        client.connected = True

    async def disconnect():
        client.connected = False

    client.start = AsyncMock(side_effect=start)
    client.disconnect = AsyncMock(side_effect=disconnect)
    factory = Mock(return_value=client)
    monkeypatch.setattr(telethon_utils, "TelegramClient", factory)
    monkeypatch.setattr(telethon_utils, "_client_lock", asyncio.Lock())
    return config, client


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "read", "dump"])
async def test_horoscope_update_disconnects_after_success_or_error(
    telethon_stub, monkeypatch, failure
):
    config, client = telethon_stub
    tracker = godnoscop_tracker.GodnoscopTracker(config)
    monkeypatch.setattr(tracker, "parse_post", lambda text: ("ОВЕН", text))
    dump = Mock(side_effect=OSError("dump failed") if failure == "dump" else None)
    monkeypatch.setattr(tracker, "dump_data", dump)

    async def messages(*args, **kwargs):
        yield SimpleNamespace(text="test horoscope")
        if failure == "read":
            raise OSError("read failed")

    client.iter_messages = messages
    if failure:
        with pytest.raises(OSError, match=f"{failure} failed"):
            await tracker.update_godnoscopes()
    else:
        result = await tracker.update_godnoscopes()
        assert result["data"] == {"ОВЕН": "test horoscope"}
    assert dump.call_count == (0 if failure == "read" else 1)
    client.start.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert not client.connected


@pytest.mark.asyncio
@pytest.mark.parametrize("second_reader", ["horoscope", "tldr"])
@pytest.mark.parametrize("cancel_disconnect", [False, True])
async def test_shared_session_waits_for_full_lifecycle(
    telethon_stub, monkeypatch, second_reader, cancel_disconnect
):
    config, _ = telethon_stub
    reading = asyncio.Event()
    release_read = asyncio.Event()
    disconnecting = asyncio.Event()
    release_disconnect = asyncio.Event()
    second_started = asyncio.Event()
    clients = []
    active = []

    def create_client(*args):
        assert not active, "A second client opened the same session file"
        client = SimpleNamespace()
        first = not clients
        clients.append(client)

        async def start():
            active.append(client)

        async def messages(*args, **kwargs):
            if first:
                reading.set()
                await release_read.wait()
            yield SimpleNamespace(
                id=1, text="test", sender=SimpleNamespace(username="reader", bot=False)
            )

        async def cleanup():
            if first:
                disconnecting.set()
                await release_disconnect.wait()
            active.remove(client)

        def disconnect():
            return asyncio.shield(asyncio.create_task(cleanup()))

        client.start = AsyncMock(side_effect=start)
        client.iter_messages = messages
        client.disconnect = Mock(side_effect=disconnect)
        return client

    monkeypatch.setattr(telethon_utils, "TelegramClient", create_client)

    async def second_fetch():
        second_started.set()
        if second_reader == "horoscope":
            return await godnoscop_tracker.GodnoscopTracker(config).update_godnoscopes()
        return await history.fetch_chat_text_messages(2, 10, config=config)

    first = asyncio.create_task(history.fetch_chat_text_messages(1, 10, config=config))
    await reading.wait()
    second = asyncio.create_task(second_fetch())
    try:
        await second_started.wait()
        assert len(clients) == 1
        release_read.set()
        await disconnecting.wait()
        assert len(clients) == 1
        if cancel_disconnect:
            for _ in range(2):
                first.cancel()
                await asyncio.sleep(0)
                assert not first.done()
                assert len(clients) == 1
        release_disconnect.set()
        if cancel_disconnect:
            with pytest.raises(asyncio.CancelledError):
                await first
            await second
        else:
            await asyncio.gather(first, second)
        assert len(clients) == 2
        assert not active
        for client in clients:
            client.disconnect.assert_called_once()
    finally:
        release_read.set()
        release_disconnect.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_overlapping_horoscopes_keep_first_readers_results(telethon_stub, monkeypatch):
    config, client = telethon_stub
    tracker = godnoscop_tracker.GodnoscopTracker(config)
    monkeypatch.setattr(tracker, "parse_post", lambda text: (text.split(":")[0], text))
    reading = asyncio.Event()
    release_read = asyncio.Event()
    second_started = asyncio.Event()
    reads = 0

    async def messages(*args, **kwargs):
        nonlocal reads
        reads += 1
        current = reads
        yield SimpleNamespace(text=f"ОВЕН:{current}")
        if current == 1:
            reading.set()
            await release_read.wait()
        yield SimpleNamespace(text=f"ТЕЛЕЦ:{current}")

    client.iter_messages = messages

    async def second_fetch():
        second_started.set()
        return await tracker.get_horoscope("ТЕЛЕЦ")

    first = asyncio.create_task(tracker.get_horoscope("ОВЕН"))
    await reading.wait()
    second = asyncio.create_task(second_fetch())
    try:
        await second_started.wait()
        assert tracker.godnoscopes["data"] == {"ОВЕН": "ОВЕН:1"}
        release_read.set()
        assert await asyncio.gather(first, second) == ["ОВЕН:1", "ТЕЛЕЦ:2"]
        assert client.disconnect.await_count == 2
    finally:
        release_read.set()
        await asyncio.gather(first, second, return_exceptions=True)


@pytest.mark.asyncio
async def test_horoscope_update_disconnects_when_cancelled(telethon_stub):
    config, client = telethon_stub
    reading = asyncio.Event()

    async def messages(*args, **kwargs):
        reading.set()
        await asyncio.Event().wait()
        yield SimpleNamespace(text="unreachable")

    client.iter_messages = messages
    task = asyncio.create_task(
        godnoscop_tracker.GodnoscopTracker(config).update_godnoscopes()
    )
    await reading.wait()
    assert client.connected
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.disconnect.assert_awaited_once()
    assert not client.connected


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", ["horoscope", "tldr"])
@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_client_partial_start_disconnects(telethon_stub, owner, failure):
    config, client = telethon_stub
    started = asyncio.Event()

    async def partial_start():
        client.connected = True
        started.set()
        if failure == "error":
            raise OSError("start failed")
        await asyncio.Event().wait()

    client.start.side_effect = partial_start
    create = (
        godnoscop_tracker.GodnoscopTracker(config).update_godnoscopes()
        if owner == "horoscope"
        else history.fetch_chat_text_messages(1, 10, config=config)
    )
    if failure == "error":
        with pytest.raises(OSError, match="start failed"):
            await create
    else:
        task = asyncio.create_task(create)
        await started.wait()
        assert client.connected
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    client.start.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert not client.connected
