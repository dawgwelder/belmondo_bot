import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

telegram = ModuleType("telegram")


class InlineKeyboardButton:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


telegram.InlineKeyboardButton = InlineKeyboardButton
telegram.InlineKeyboardMarkup = InlineKeyboardMarkup
telegram.Update = object
telegram_ext = ModuleType("telegram.ext")
telegram_ext.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)

sys.modules.setdefault("telegram", telegram)
sys.modules.setdefault("telegram.ext", telegram_ext)
sys.modules.setdefault("config", SimpleNamespace(logger=Mock()))

from handlers.roulette import roulette_callback, start_roulette


def _update(user_id, name):
    query = SimpleNamespace(
        data="roulette:test:pull",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    user = SimpleNamespace(id=user_id, username=None, full_name=name)
    return SimpleNamespace(
        callback_query=query,
        effective_user=user,
        effective_chat=SimpleNamespace(id=-100),
    )


@pytest.mark.asyncio
async def test_roulette_tracks_safe_pull_and_losing_pull():
    game = {
        "id": "test",
        "bullet_chamber": 2,
        "pulls_taken": 0,
        "players": {},
        "player_order": [],
        "loser": None,
    }
    context = SimpleNamespace(chat_data={"roulette_games": {"test": game}})

    first_update = _update(1, "Alice")
    await roulette_callback(first_update, context)

    assert context.chat_data["roulette_games"]["test"]["pulls_taken"] == 1
    assert game["players"]["1"]["pulls"] == 1
    first_update.callback_query.answer.assert_awaited_once_with("Щёлк. Пусто.")
    first_text = first_update.callback_query.edit_message_text.await_args.args[0]
    assert "Нажатий на курок осталось: 5 из 6" in first_text

    second_update = _update(2, "Bob")
    await roulette_callback(second_update, context)

    assert context.chat_data["roulette_games"] == {}
    second_update.callback_query.answer.assert_awaited_once_with("Бах.")
    final_text = second_update.callback_query.edit_message_text.await_args.args[0]
    assert "Проиграл: Bob" in final_text
    assert "Alice: 1 раз" in final_text
    assert "Bob: 1 раз" in final_text


@pytest.mark.asyncio
async def test_roulette_can_start_from_callback_message():
    source_message = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(message=source_message)
    update = SimpleNamespace(
        message=None,
        callback_query=query,
        effective_chat=SimpleNamespace(id=-100),
    )
    context = SimpleNamespace(chat_data={})

    await start_roulette(update, context)

    assert len(context.chat_data["roulette_games"]) == 1
    args, kwargs = source_message.reply_text.await_args
    assert "Русская рулетка началась." in args[0]
    assert kwargs["reply_markup"].inline_keyboard[0][0].text == "Нажать на курок"
