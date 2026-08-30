import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.spy_game as spy_handlers
from spy_game.models import SpawnEvent
from spy_game.narrator import (
    EventNarrative,
    LLMNarrator,
    NarrationUnavailable,
    ResilientNarrator,
    TemplateNarrator,
    build_narrator,
)
from spy_game.settings import SpySettings


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
EVENT = SpawnEvent("abcdef123456", -100, "recruitment", NOW + timedelta(minutes=3))


def settings(tmp_path: Path, *, enabled: bool) -> SpySettings:
    return SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "spy.sqlite3",
        allowed_chat_ids=frozenset({-100}),
        llm_narrator_enabled=enabled,
        llm_narrator_timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_template_narrator_is_deterministic():
    narrator = TemplateNarrator()
    first = await narrator.narrate(EVENT)
    second = await narrator.narrate(EVENT)
    assert first == second
    assert first.source == "template"
    assert len(first.body) > 20


@pytest.mark.asyncio
async def test_llm_narrator_reuses_structured_games_contract():
    captured = {}

    async def request(prompt, validator, *, corrective_hint):
        captured["prompt"] = prompt
        captured["hint"] = corrective_hint
        return validator(
            {
                "body": (
                    "На мокрой мостовой появился человек с чужим зонтом, "
                    "а затем слишком быстро растворился в тумане."
                )
            }
        )

    narrative = await LLMNarrator(request).narrate(EVENT)

    assert narrative.source == "llm"
    assert "<untrusted_json>" in captured["prompt"]
    assert '"event_type": "recruitment"' in captured["prompt"]
    assert '"body"' in captured["hint"]


@pytest.mark.asyncio
async def test_llm_narrator_rejects_gameplay_claims_even_in_valid_json():
    async def request(_prompt, validator, *, corrective_hint):
        del corrective_hint
        return validator({"body": "Нажми кнопку и получи сто агентов немедленно."})

    with pytest.raises(NarrationUnavailable):
        await LLMNarrator(request).narrate(EVENT)

    resilient = ResilientNarrator(
        LLMNarrator(request),
        TemplateNarrator(),
        1,
    )
    assert (await resilient.narrate(EVENT)).source == "template"


@pytest.mark.asyncio
async def test_resilient_narrator_falls_back_after_timeout():
    class SlowNarrator:
        async def narrate(self, _event):
            await asyncio.sleep(1)
            return EventNarrative("late", "llm")

    narrator = ResilientNarrator(SlowNarrator(), TemplateNarrator(), 0.01)
    narrative = await narrator.narrate(EVENT)
    assert narrative.source == "template"


def test_narrator_is_opt_in(tmp_path):
    assert isinstance(
        build_narrator(settings(tmp_path, enabled=False)), TemplateNarrator
    )
    assert isinstance(
        build_narrator(settings(tmp_path, enabled=True)), ResilientNarrator
    )


def test_narrator_settings_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("SPY_GAME_LLM_NARRATOR_ENABLED", "true")
    monkeypatch.setenv("SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS", "11")
    config = SpySettings.from_env("dev")
    assert config.llm_narrator_enabled is True
    assert config.llm_narrator_timeout_seconds == 11


@pytest.mark.asyncio
async def test_event_publisher_uses_narrator_body_in_rich_message(monkeypatch):
    class FakeNarrator:
        async def narrate(self, _event):
            return EventNarrative("Сгенерированная кинематографичная завязка.", "llm")

    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 55}})
    monkeypatch.setattr(spy_handlers, "send_rich_message", send_rich)
    context = SimpleNamespace(
        bot_data={"spy_narrator": FakeNarrator()},
        bot=SimpleNamespace(token="TOKEN", send_message=AsyncMock()),
    )

    message_id = await spy_handlers.publish_spy_event(context, EVENT)

    assert message_id == 55
    blocks = send_rich.await_args.args[2]
    assert blocks[1] == {
        "type": "paragraph",
        "text": "Сгенерированная кинематографичная завязка.",
    }
    assert "Первый подтверждённый контакт" in blocks[-1]["text"]
    assert send_rich.await_args.kwargs["reply_markup"]["inline_keyboard"]
