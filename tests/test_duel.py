import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.modules.setdefault(
    "config",
    SimpleNamespace(client=Mock(), logger=Mock(), TELEGRAM_MAX_MESSAGE_LENGTH=4096),
)

import const
from handlers.duel import (
    _duel_ai_messages,
    _extract_json,
    _fallback_judgement,
    _naturalize_judgement_text,
    duel_accept_timeout,
    duel_callback,
)


def _duel_data():
    return {
        "id": "test",
        "status": "choosing",
        "players": {
            "challenger": {"id": 1, "name": "Challenger"},
            "opponent": {"id": 2, "name": "Opponent"},
        },
        "scenario": {
            "title": "Test",
            "setting": "Test setting",
            "condition": "Test condition",
        },
        "actions": {},
    }


def test_extract_json_from_fenced_response():
    assert _extract_json('```json\n{"winner": "challenger"}\n```') == {
        "winner": "challenger"
    }


def test_duel_ai_messages_always_start_with_belmondo_system_prompt():
    messages = _duel_ai_messages("test prompt")

    assert messages[0] == {"role": "system", "content": const.professional_prompt}
    assert messages[1] == {"role": "user", "content": "test prompt"}


def test_fallback_judgement_uses_action_cycle():
    duel = _duel_data()
    duel["actions"] = {"1": "defend", "2": "attack"}

    assert _fallback_judgement(duel)["winner"] == "challenger"


def test_judgement_text_replaces_internal_roles_with_player_names():
    duel = _duel_data()
    duel["players"]["challenger"]["name"] = "@dawgwelder"
    duel["players"]["opponent"]["name"] = "@NorthernPenguin"

    text = "Challenger обманул opponent, но OPPONENT попытался ответить."

    assert _naturalize_judgement_text(text, duel) == (
        "@dawgwelder обманул @NorthernPenguin, "
        "но @NorthernPenguin попытался ответить."
    )


@pytest.mark.asyncio
async def test_outsider_cannot_choose_a_move():
    query = SimpleNamespace(
        data="duel:test:move:attack",
        answer=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=999, username="outsider"),
    )
    context = SimpleNamespace(chat_data={"duels": {"test": _duel_data()}})

    await duel_callback(update, context)

    query.answer.assert_awaited_once_with(
        "Наблюдатели не могут вмешиваться в дуэль.", show_alert=True
    )
    assert context.chat_data["duels"]["test"]["actions"] == {}


@pytest.mark.asyncio
async def test_only_opponent_can_accept_challenge():
    duel = _duel_data()
    duel["status"] = "pending"
    query = SimpleNamespace(
        data="duel:test:accept",
        answer=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=1, username="challenger"),
    )
    context = SimpleNamespace(chat_data={"duels": {"test": duel}})

    await duel_callback(update, context)

    query.answer.assert_awaited_once_with(
        "Эта кнопка предназначена только вызванному дуэлянту.", show_alert=True
    )
    assert duel["status"] == "pending"


@pytest.mark.asyncio
async def test_mentioned_username_is_bound_to_user_id_on_accept():
    duel = _duel_data()
    duel["status"] = "pending"
    duel["players"]["opponent"] = {
        "id": None,
        "name": "@opponent",
        "username": "opponent",
    }
    query = SimpleNamespace(
        data="duel:test:accept",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=42,
            username="Opponent",
            full_name="Opponent",
        ),
    )
    context = SimpleNamespace(chat_data={"duels": {"test": duel}})

    await duel_callback(update, context)

    assert duel["status"] == "choosing"
    assert duel["players"]["opponent"]["id"] == 42


@pytest.mark.asyncio
async def test_pending_challenge_expires_after_timeout():
    duel = _duel_data()
    duel["status"] = "pending"
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    context = SimpleNamespace(
        chat_data={"duels": {"test": duel}},
        job=SimpleNamespace(
            data={"duel_id": "test", "chat_id": -100, "message_id": 77}
        ),
        bot=bot,
    )

    await duel_accept_timeout(context)

    assert context.chat_data["duels"] == {}
    bot.edit_message_text.assert_awaited_once()
