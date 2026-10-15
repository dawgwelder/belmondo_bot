"""Closed messages stay compact without interrupting game settlement."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from handlers.spy import (
    callback_contacts,
    callback_investigation,
    callback_operations,
    jobs,
    transport,
)
from spy_game.models import (
    AgentHolding,
    ClaimStatus,
    CooperativeResult,
    CooperativeStatus,
    DeadDropResult,
    DeathOperationResult,
    DeathOperationStatus,
    DropReward,
    EconomyStatus,
    ExchangeResult,
    ExpiredEvent,
    FindMoleGameRun,
    FindMoleGameStatus,
    InterceptResult,
    InterceptStatus,
    NpcResult,
    NpcStatus,
    RecruitmentProgress,
    Reward,
    TickResult,
)
from spy_game.telegram_messages import (
    compact_event_message,
    compact_event_query,
    publish_event_result,
)


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
        edited = await compact_event_query(target, "✅ Тайник обыскан.")
    else:
        edited = await compact_event_message(target, -100, 77, "✅ Тайник обыскан.")
    assert edited == (not isinstance(failure, RuntimeError))
    target.edit_message_text.assert_awaited_once()
    assert target.edit_message_text.await_args.kwargs["text"] == "✅ Тайник обыскан."
    assert target.edit_message_text.await_args.kwargs["reply_markup"] is None
    assert target.edit_message_reply_markup.await_count == int(
        isinstance(failure, RuntimeError)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_id,edit_error,markup_error,expected_sends",
    [
        (77, None, None, 0),
        (77, BadRequest("Message is not modified"), None, 0),
        (77, BadRequest("Message to edit not found"), None, 1),
        (77, RuntimeError("offline"), RuntimeError("offline"), 1),
        (None, None, None, 1),
    ],
)
async def test_result_is_sent_only_when_original_cannot_be_edited(
    message_id, edit_error, markup_error, expected_sends
):
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=edit_error),
        edit_message_reply_markup=AsyncMock(side_effect=markup_error),
        send_message=AsyncMock(),
    )
    text = "✅ СПЕЦИАЛЬНАЯ СДЕЛКА\n@bond получил: 🏛 Резидент ×2."
    await publish_event_result(bot, -100, message_id, text)
    assert bot.edit_message_text.await_count == int(message_id is not None)
    if message_id is not None:
        bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100, message_id=message_id, text=text, reply_markup=None
        )
    assert bot.send_message.await_count == expected_sends
    if expected_sends:
        bot.send_message.assert_awaited_once_with(chat_id=-100, text=text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,method,result,expected",
    [
        (
            callback_contacts.handle_npc,
            "interact_with_npc",
            NpcResult(
                NpcStatus.SUCCESS, "event", "recipe", DropReward("agent", "resident", 2)
            ),
            ("✅ СПЕЦИАЛЬНАЯ СДЕЛКА", "получил: 🏛 Резидент ×2."),
        ),
        (
            callback_contacts.handle_exchange,
            "exchange_with_handler",
            ExchangeResult(
                EconomyStatus.SUCCESS, "event", "recipe", Reward("informant", 2)
            ),
            ("✅ ОБМЕН ЗАВЕРШЁН", "Новый агент: 🕵️ Осведомитель ×2"),
        ),
        (
            callback_operations.handle_cooperate,
            "contribute_cooperative",
            CooperativeResult(
                CooperativeStatus.COMPLETED,
                "event",
                contributions=3,
                reward=Reward("informant", 2),
                radio_bonus_user_ids=(1,),
            ),
            (
                "✅ ОПЕРАЦИЯ ЗАВЕРШЕНА",
                "3 участников",
                "Каждому начислено: 🕵️ Осведомитель ×2",
                "Бонус рации",
            ),
        ),
        (
            callback_operations.handle_death,
            "run_death_operation",
            DeathOperationResult(
                DeathOperationStatus.WON,
                "event",
                staked=(AgentHolding("informant", 10),),
                rewards=(Reward("informant", 20), Reward("double_agent", 1)),
            ),
            ("✅ НЕВОЗМОЖНОЕ ВЫПОЛНЕНО", "сеть из 10 агентов", "×2", "Двойной агент"),
        ),
        (
            callback_operations.handle_death,
            "run_death_operation",
            DeathOperationResult(
                DeathOperationStatus.LOST,
                "event",
                staked=(AgentHolding("informant", 10),),
            ),
            ("☠️ СВЯЗЬ ПОТЕРЯНА", "10 агентов. Никто не вернулся."),
        ),
        (
            callback_investigation.handle_mole,
            "accuse_find_mole_event",
            FindMoleGameRun(
                FindMoleGameStatus.WON,
                item_reward=DropReward("item", "fake_passport", 1),
                agent_reward=Reward("informant", 3),
            ),
            ("✅ КРОТ РАСКРЫТ", "🛂 Поддельный паспорт ×1", "🕵️ Осведомитель ×3"),
        ),
        (
            callback_investigation.handle_search,
            "search_dead_drop",
            DeadDropResult(
                ClaimStatus.WON, "event", DropReward("item", "intel_file", 1)
            ),
            ("✅ ТАЙНИК ВСКРЫТ", "нашёл: 📁 Разведданные ×1."),
        ),
        (
            callback_investigation.handle_search,
            "search_dead_drop",
            DeadDropResult(ClaimStatus.WON, "event", DropReward("empty", None, 0)),
            ("✅ ТАЙНИК ВСКРЫТ", "тайник оказался пуст"),
        ),
        (
            callback_investigation.handle_intercept,
            "answer_intercept",
            InterceptResult(
                InterceptStatus.CORRECT, "event", DropReward("item", "access_code", 1)
            ),
            ("✅ ШИФР РАСКРЫТ", "🔐 Код доступа ×1"),
        ),
        (
            callback_investigation.handle_intercept,
            "answer_intercept",
            InterceptResult(InterceptStatus.INCORRECT, "event"),
            ("❌ КАНАЛ ПОТЕРЯН", "оказался ложным следом"),
        ),
    ],
)
@pytest.mark.parametrize("username", ["bond", None])
async def test_callback_replaces_source_with_complete_result(
    monkeypatch, handler, method, result, expected, username
):
    service = SimpleNamespace(
        settings=SimpleNamespace(death_operation_reward_multiplier=2),
        **{method: AsyncMock(return_value=result)},
    )
    monkeypatch.setattr(f"{handler.__module__}._service", lambda context: service)
    send_rich = AsyncMock()
    monkeypatch.setattr(transport, "send_rich_message", send_rich)
    query = SimpleNamespace(
        id="attempt",
        message=SimpleNamespace(message_id=77),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=1, username=username, full_name="Private Name"
        ),
        effective_chat=SimpleNamespace(id=-100),
    )
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
        send_message=AsyncMock(),
    )
    await handler(update, SimpleNamespace(bot=bot), "recipe", "event")
    bot.edit_message_text.assert_awaited_once()
    edit = bot.edit_message_text.await_args.kwargs
    assert (edit["chat_id"], edit["message_id"], edit["reply_markup"]) == (
        -100,
        77,
        None,
    )
    assert all(part in edit["text"] for part in expected)
    assert "Private Name" not in edit["text"]
    if handler is not callback_operations.handle_cooperate:
        assert ("@bond" if username else "Скрытый агент") in edit["text"]
    bot.send_message.assert_not_awaited()
    send_rich.assert_not_awaited()
    query.edit_message_text.assert_not_awaited()
    query.answer.assert_awaited_once()


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
