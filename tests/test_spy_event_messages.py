"""Closed messages stay compact without interrupting game settlement."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from handlers.spy import jobs
from spy_game.models import ExpiredEvent, RecruitmentProgress, TickResult
from spy_game.telegram_messages import compact_event_message, compact_event_query


@pytest.mark.asyncio
@pytest.mark.parametrize("via_callback", [False, True])
@pytest.mark.parametrize(
    "failure", [None, BadRequest("Message is not modified"), RuntimeError("offline")]
)
async def test_compact_message_and_keyboard_fallback(via_callback, failure):
    target = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=failure),
        edit_message_reply_markup=AsyncMock(),
    )
    if via_callback:
        await compact_event_query(target, "✅ Тайник обыскан.")
    else:
        await compact_event_message(target, -100, 77, "✅ Тайник обыскан.")
    target.edit_message_text.assert_awaited_once()
    assert target.edit_message_text.await_args.kwargs["text"] == "✅ Тайник обыскан."
    assert target.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert target.edit_message_reply_markup.await_count == int(
        isinstance(failure, RuntimeError)
    )


@pytest.mark.asyncio
async def test_tick_compacts_expired_events_and_preserves_personal_mission_results(
    monkeypatch,
):
    service = SimpleNamespace(
        tick=AsyncMock(
            return_value=TickResult(
                expired=(
                    ExpiredEvent("recruit", -100, 1, "recruitment"),
                    ExpiredEvent("chase", -100, 2, "chase"),
                    ExpiredEvent(
                        "mission", -100, 3, "death_operation", result_managed=True
                    ),
                    ExpiredEvent("cancelled", -100, 4, "npc", cancelled=True),
                )
            )
        ),
        get_recruitment_progress=AsyncMock(
            return_value=RecruitmentProgress(
                "recruit",
                1,
                3,
                ("@bond",),
                completed=True,
            )
        ),
    )
    monkeypatch.setattr(jobs, "_service", lambda context: service)
    monkeypatch.setattr(jobs.death_mission_ui, "publish_pending", AsyncMock())
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock()
    )
    await jobs.spy_game_tick(SimpleNamespace(bot=bot, bot_data={}))
    edits = {
        call.kwargs["message_id"]: call.kwargs
        for call in bot.edit_message_text.await_args_list
    }
    assert set(edits) == {1, 2, 4}
    assert (
        edits[1]["text"]
        == "⌛ Набор: время истекло.\nКонтакты: 1/3\nПодтверждены: @bond"
    )
    assert edits[2]["text"] == "⌛ Погоня: время истекло."
    assert edits[4]["text"] == "🚫 Специальная сделка: событие отменено."
    assert all(edit["reply_markup"] is None for edit in edits.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "expired"])
async def test_personal_mole_failure_keeps_shared_event_open(monkeypatch, outcome):
    from handlers.spy import callback_investigation
    from spy_game.models import FindMoleGameStatus

    service = SimpleNamespace(
        accuse_find_mole_event=AsyncMock(
            return_value=SimpleNamespace(status=FindMoleGameStatus(outcome))
        ),
        get_chat_status=AsyncMock(return_value=SimpleNamespace(active_event_id="mole")),
    )
    monkeypatch.setattr(callback_investigation, "_service", lambda context: service)
    query = SimpleNamespace(
        id="attempt",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1, username="bond", full_name="Private Name"),
        effective_chat=SimpleNamespace(id=-100),
    )
    await callback_investigation.handle_mole(
        update, SimpleNamespace(), "mole_suspect", "mole"
    )
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_not_awaited()
