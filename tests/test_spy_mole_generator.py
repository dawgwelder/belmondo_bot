import asyncio
import copy
import json
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.spy.publication import publish_spy_event
from handlers.spy import transport
from spy_game.mole_generator import MoleCaseGenerator
from spy_game.models import FindMoleGameStatus
from spy_game.narrator import TemplateNarrator
from spy_game.service import SpyGameService
from test_spy_game import settings, CHAT_ID, NOW, FixedRandom


def payload():
    return {
        "title": "Ночной архив",
        "briefing": "Из архива похищен список связных.",
        "features": ["Вход", "Пропуск", "След"],
        "required": ["Север", "Синий", "Мел"],
        "suspects": [
            {"codename": "Лис", "role": "Курьер", "facts": ["Север", "Синий", "Мел"]},
            {
                "codename": "Сова",
                "role": "Наблюдатель",
                "facts": ["Юг", "Синий", "Мел"],
            },
            {
                "codename": "Крот",
                "role": "Архивариус",
                "facts": ["Север", "Красный", "Мел"],
            },
            {
                "codename": "Кедр",
                "role": "Связной",
                "facts": ["Север", "Синий", "Песок"],
            },
        ],
    }


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda p: p.update(extra=True),
        lambda p: p.update(suspects=[]),
        lambda p: p["suspects"][1].update(facts=p["required"]),
        lambda p: p["suspects"][0].update(facts=["Юг", "Красный", "Песок"]),
        lambda p: p["suspects"][1].update(codename="Лис"),
        lambda p: p.update(features=["Вход", "Вход", "След"]),
        lambda p: p.update(briefing="<script>"),
        lambda p: p.update(required=[None, None, None]),
        lambda p: p.update(title="x" * 81),
        lambda p: p["suspects"][1].update(facts=["север", "Синий", "Мел"]),
        lambda p: p["suspects"][1].update(facts=["Север ", "Синий", "Мел"]),
    ],
)
def test_invalid_or_ambiguous_generated_case_rejected(corrupt):
    value = copy.deepcopy(payload())
    corrupt(value)
    assert MoleCaseGenerator.validate(value) is None


@pytest.mark.asyncio
async def test_generated_case_saved_once_shared_by_inline_html5_and_restart(
    tmp_path, monkeypatch
):
    config = replace(settings(tmp_path), llm_mole_enabled=True, html5_mole_enabled=True)
    service = SpyGameService(config, rng=FixedRandom())
    request = AsyncMock(return_value=payload())
    service.mole_generator = MoleCaseGenerator(config, FixedRandom(), request)
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        event = (
            await service.manual_spawn(CHAT_ID, event_type="find_mole", now=NOW)
        ).event
        assert len(event.mole_case.solution_candidates) == 1
        assert event.config_id.startswith("generated_")
        assert request.await_count == 1
        assert not (
            await service.manual_spawn(CHAT_ID, event_type="find_mole", now=NOW)
        ).ok
        assert request.await_count == 1
        captured = {}

        async def send(*args, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(message_id=42)

        monkeypatch.setattr(
            transport,
            "send_rich_message",
            AsyncMock(side_effect=RuntimeError("no rich support")),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(token="fake", send_message=send),
            bot_data={"spy_game": service, "spy_narrator": TemplateNarrator()},
        )
        assert await publish_spy_event(context, event) == 42
        assert "Север" in captured["text"] and "Досье" in captured["text"]
        await service.attach_message(event.event_id, 42)
        run = await service.start_find_mole_game(
            chat_id=CHAT_ID,
            message_id=42,
            user_id=1,
            username=None,
            display_name=None,
            now=NOW,
        )
        assert run.status is FindMoleGameStatus.READY
        public = json.dumps(asdict(run), default=str)
        assert "solution_tags" not in public and "evidence_tags" not in public
        solution = event.mole_case.correct_suspect_id
        await service.close()
        service = SpyGameService(config, rng=FixedRandom())
        service.mole_generator.request = AsyncMock(
            side_effect=AssertionError("must not regenerate")
        )
        await service.initialize(now=NOW + timedelta(seconds=2))
        restored = await service.get_find_mole_game(
            run.launch_token, now=NOW + timedelta(seconds=3)
        )
        assert (
            restored.suspects == run.suspects
            and restored.status is FindMoleGameStatus.READY
        )
        won = await service.accuse_find_mole_event(
            event_id=event.event_id,
            suspect_id=solution,
            idempotency_key="generated-win",
            chat_id=CHAT_ID,
            user_id=2,
            username=None,
            display_name=None,
            now=NOW + timedelta(seconds=4),
        )
        assert won.status is FindMoleGameStatus.WON
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invalid", "exception", "timeout", "disabled"])
async def test_generator_fallback_is_bounded_and_valid(tmp_path, mode):
    config = replace(
        settings(tmp_path),
        llm_mole_enabled=mode != "disabled",
        llm_mole_timeout_seconds=0.01,
    )

    async def request(*args, **kwargs):
        if mode == "disabled":
            raise AssertionError("disabled generator called provider")
        if mode == "exception":
            raise RuntimeError("provider down")
        if mode == "timeout":
            await asyncio.sleep(10)
        return {"bad": "json"}

    generator = MoleCaseGenerator(config, FixedRandom(), request)
    case = await generator.generate()
    assert len(case.solution_candidates) == 1 and case in config.mole_cases


@pytest.mark.asyncio
async def test_scheduler_generates_mole_outside_transaction(tmp_path):
    from spy_game.director import DirectorDecision

    config = replace(settings(tmp_path), llm_mole_enabled=True)
    service = SpyGameService(config, rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await service.database.transaction(
            lambda c: c.execute(
                "UPDATE chat_state SET story_arc='mole_hunt', story_stage=3 WHERE chat_id=?",
                (CHAT_ID,),
            ),
            immediate=True,
        )
        service.director = SimpleNamespace(
            choose_event=AsyncMock(
                return_value=DirectorDecision("find_mole", "paranoid", "mole_hunt", 3)
            )
        )

        async def request(*args, **kwargs):
            # This read would deadlock if generation held the SQLite executor.
            await asyncio.wait_for(service.get_chat_status(CHAT_ID), timeout=1)
            return payload()

        service.mole_generator.request = request
        await service.record_activity(CHAT_ID, 1, now=NOW)
        await service.record_activity(CHAT_ID, 2, now=NOW)
        result = await service.tick(now=NOW)
        assert len(result.spawned) == 1
        assert result.spawned[0].mole_case.id.startswith("generated_")
    finally:
        await service.close()
