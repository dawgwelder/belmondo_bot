import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
import pytest_asyncio

from spy_game.database import SQLiteDatabase
from spy_game.narrator import (
    EventNarrative,
    PersistentNarrator,
    TemplateNarrator,
    build_narrator,
)
from test_spy_narrator import EVENT, NOW, settings


@pytest_asyncio.fixture
async def database(tmp_path):
    db = SQLiteDatabase(tmp_path / "spy.sqlite3")
    await db.initialize()
    try:
        yield db
    finally:
        await db.close()


class Clock:
    now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds=300):
        self.now += timedelta(seconds=seconds)


class Generated:
    def __init__(self, database):
        self.events = []
        self.database = database

    async def narrate(self, event):
        # A separate DB operation must remain possible during generation.
        await self.database.transaction(lambda c: c.execute("SELECT 1"))
        self.events.append(event)
        letter = chr(ord("а") + len(self.events))
        return EventNarrative(
            f"На конверте стояла необычная отметка «{letter}». Связной молча убрал письмо.",
            "llm",
        )


async def pool(database):
    return await database.read(
        lambda c: c.execute("SELECT text FROM event_templates ORDER BY id").fetchall()
    )


@pytest.mark.asyncio
async def test_pool_fills_rotates_and_refreshes_without_recent_repeats(database):
    clock = Clock()
    generated = Generated(database)
    narrator = PersistentNarrator(database, generated, clock=clock)
    bodies = []
    for index in range(20):
        result = await narrator.narrate(replace(EVENT, event_id=f"event-{index}"))
        assert result.body not in bodies[-3:]
        bodies.append(result.body)
        clock.advance()
    assert len(generated.events) == 8
    assert len(await pool(database)) == 8
    assert generated.events[1].recent_narratives == (bodies[0],)
    clock.advance(21600)
    fresh = await narrator.narrate(replace(EVENT, event_id="refresh"))
    assert fresh.source == "llm"
    assert len(generated.events) == 9
    assert len(await pool(database)) == 8
    assert bodies[0] not in [row["text"] for row in await pool(database)]


@pytest.mark.asyncio
async def test_cooldown_and_history_survive_restart_and_are_shared_between_chats(
    database,
):
    clock = Clock()
    generated = Generated(database)
    first = PersistentNarrator(database, generated, clock=clock)
    narrative = await first.narrate(EVENT)
    restarted = SQLiteDatabase(database.path)
    await restarted.initialize()
    try:
        second = PersistentNarrator(restarted, generated, clock=clock)
        replay = await second.narrate(EVENT)
        assert replay.body == narrative.body
        next_event = await second.narrate(replace(EVENT, event_id="next"))
        assert next_event.body != narrative.body
        other_chat = await second.narrate(
            replace(EVENT, event_id="other", chat_id=-200)
        )
        assert other_chat.body == narrative.body
        assert len(generated.events) == 1
        clock.advance()
        await second.narrate(replace(EVENT, event_id="later"))
        assert len(generated.events) == 2
    finally:
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failure", "duplicate", "invalid"])
async def test_failed_or_duplicate_generation_still_consumes_cooldown(database, mode):
    clock = Clock()
    calls = []

    class Primary:
        async def narrate(self, event):
            calls.append(event)
            if mode == "failure":
                raise TimeoutError
            body = "На вокзале ждёт незнакомец в сером пальто."
            if mode == "invalid":
                body = "Нажми кнопку и получи сто агентов немедленно."
            return EventNarrative(body, "llm")

    narrator = PersistentNarrator(database, Primary(), clock=clock)
    first = await narrator.narrate(EVENT)
    clock.advance()
    second = await narrator.narrate(replace(EVENT, event_id="next"))
    third = await narrator.narrate(replace(EVENT, event_id="immediate"))
    assert len(calls) == 2
    assert len({first.body, second.body, third.body}) == 3
    assert len(await pool(database)) == (1 if mode == "duplicate" else 0)


@pytest.mark.asyncio
async def test_concurrent_requests_claim_only_one_generation(database):
    generated = Generated(database)
    first = PersistentNarrator(database, generated, clock=Clock())
    second = PersistentNarrator(database, generated, clock=Clock())
    a, b = await asyncio.gather(first.narrate(EVENT), second.narrate(EVENT))
    assert a.body == b.body
    assert len(generated.events) == 1
    rows = await database.read(
        lambda c: c.execute("SELECT count(*) FROM event_narratives").fetchone()[0]
    )
    assert rows == 1


@pytest.mark.asyncio
async def test_context_does_not_reuse_legacy_or_other_mission_prose(database):
    await database.transaction(
        lambda c: c.execute(
            "INSERT INTO event_templates(event_type,tone,text) VALUES (?,?,?)",
            (
                EVENT.event_type,
                EVENT.tone,
                "Старый текст с неизвестным сюжетным контекстом.",
            ),
        )
    )
    generated = Generated(database)
    narrator = PersistentNarrator(database, generated, clock=Clock())
    original = await narrator.narrate(EVENT)
    assert original.source == "llm"
    for index, change in enumerate(
        (
            {"config_id": "other"},
            {"story_hook": "section_7"},
            {"lore_context": ("Другой сюжет",)},
        )
    ):
        result = await narrator.narrate(
            replace(EVENT, event_id=f"changed-{index}", chat_id=-200, **change)
        )
        assert result.source == "template"
    assert len(generated.events) == 1


@pytest.mark.asyncio
async def test_llm_disabled_rotates_templates_for_every_event_type(database, tmp_path):
    config = settings(tmp_path, enabled=False)
    narrator = build_narrator(config, database)
    assert isinstance(narrator, PersistentNarrator)
    assert narrator.primary is None
    for weight in config.event_weights:
        bodies = []
        event = replace(EVENT, event_type=weight.event_type)
        assert len(set(TemplateNarrator.variants(event))) == 8
        for index in range(12):
            result = await narrator.narrate(
                replace(event, event_id=f"{weight.event_type}-{index}")
            )
            assert result.source == "template"
            assert result.body not in bodies[-3:]
            bodies.append(result.body)
    assert await pool(database) == []
