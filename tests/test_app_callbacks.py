"""Callback routing regressions: game buttons must never request horoscopes."""

import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User


sys.modules.setdefault(
    "config",
    SimpleNamespace(
        client=Mock(),
        logger=Mock(),
        TELEGRAM_MAX_MESSAGE_LENGTH=4096,
        tz=timezone.utc,
    ),
)


@pytest.fixture
def callback_modules(monkeypatch, tmp_path):
    # Bootstrap imports must not read credentials or a real horoscope cache.
    monkeypatch.setattr(
        sys.modules["config"],
        "config",
        {"paths": {"gonoscopes_path": str(tmp_path / "horoscopes.json")}},
        raising=False,
    )
    import app
    import handlers.godnoscope as godnoscope

    return app, godnoscope


def callback_update(data=None, *, game_short_name=None):
    user = User(id=42, first_name="Agent", is_bot=False)
    message = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=Chat(id=-100123, type="supergroup"),
    )
    return Update(
        update_id=1,
        callback_query=CallbackQuery(
            id="callback-1",
            from_user=user,
            chat_instance="test-chat",
            message=message,
            data=data,
            game_short_name=game_short_name,
        ),
    )


@pytest.mark.parametrize(
    ("data", "game_short_name", "expected"),
    [
        ("spy:claim:c26bf6209c14", None, "spy_callback"),
        ("spy:menu:profile", None, "spy_callback"),
        ("duel:test:accept", None, "duel_callback"),
        ("game:test:join", None, "game_callback"),
        ("roulette:test:pull", None, "roulette_callback"),
        ("mp:flip", None, "magic_prediction_callback"),
        (None, "dead_drop", "spy_html5_game_launch"),
        ("unknown:action", None, None),
        ("spy:malformed", None, None),
        ("ОВЕН:unexpected", None, None),
        ("ОВЕН\n", None, None),
    ],
)
def test_callbacks_have_only_their_own_handler(
    callback_modules, data, game_short_name, expected
):
    app, _ = callback_modules
    update = callback_update(data, game_short_name=game_short_name)
    matches = [
        handler.callback.__name__
        for handler in app._build_callback_handlers()
        if handler.check_update(update)
    ]
    assert matches == ([expected] if expected else [])


@pytest.mark.asyncio
async def test_every_horoscope_button_still_routes_and_sends_forecast(
    callback_modules, monkeypatch
):
    app, godnoscope = callback_modules
    tracker = SimpleNamespace(get_horoscope=AsyncMock(return_value="Прогноз"))
    monkeypatch.setattr(godnoscope, "tracker", tracker)
    context = SimpleNamespace(
        bot_data={}, bot=SimpleNamespace(send_message=AsyncMock())
    )
    menu_message = SimpleNamespace(reply_text=AsyncMock())
    await godnoscope.godnoscope(SimpleNamespace(message=menu_message), context)
    keyboard = menu_message.reply_text.await_args.kwargs["reply_markup"]
    signs = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert len(signs) == 12

    for sign in signs:
        matches = [
            handler
            for handler in app._build_callback_handlers()
            if handler.check_update(callback_update(sign))
        ]
        assert len(matches) == 1
        assert matches[0].callback is godnoscope.button_godnoscope
        query = SimpleNamespace(data=sign, answer=AsyncMock())
        update = SimpleNamespace(
            callback_query=query, effective_chat=SimpleNamespace(id=-100123)
        )
        await matches[0].callback(update, context)
        query.answer.assert_awaited_once_with()
        tracker.get_horoscope.assert_awaited_with(sign)
        context.bot.send_message.assert_awaited_with(chat_id=-100123, text="Прогноз")

    assert tracker.get_horoscope.await_count == 12


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data", ["spy:claim:c26bf6209c14", "unknown", "ОВЕН\n", "", None, {}]
)
async def test_horoscope_handler_ignores_foreign_data_even_if_called_directly(
    callback_modules, monkeypatch, data
):
    _, godnoscope = callback_modules
    tracker = SimpleNamespace(get_horoscope=AsyncMock())
    monkeypatch.setattr(godnoscope, "tracker", tracker)
    query = SimpleNamespace(data=data, answer=AsyncMock())
    context = SimpleNamespace(
        bot_data={}, bot=SimpleNamespace(send_message=AsyncMock())
    )

    await godnoscope.button_godnoscope(SimpleNamespace(callback_query=query), context)

    query.answer.assert_not_awaited()
    tracker.get_horoscope.assert_not_awaited()
    context.bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_reported_claim_reaches_spy_service(callback_modules, monkeypatch):
    app, godnoscope = callback_modules
    from spy_game.models import ClaimStatus
    from spy_game.service import SpyGameService

    tracker = SimpleNamespace(get_horoscope=AsyncMock())
    monkeypatch.setattr(godnoscope, "tracker", tracker)
    service = AsyncMock(spec=SpyGameService)
    service.claim_event = AsyncMock(
        return_value=SimpleNamespace(status=ClaimStatus.ALREADY_CLAIMED)
    )
    service.touch_member = AsyncMock()
    bot = SimpleNamespace(answer_callback_query=AsyncMock(), send_message=AsyncMock())
    context = SimpleNamespace(bot_data={"spy_game": service}, bot=bot)
    update = callback_update("spy:claim:c26bf6209c14")
    update.callback_query.set_bot(bot)
    handler = next(
        handler
        for handler in app._build_callback_handlers()
        if handler.check_update(update)
    )

    await handler.callback(update, context)

    service.claim_event.assert_awaited_once_with(
        event_id="c26bf6209c14",
        action="claim",
        chat_id=-100123,
        user_id=42,
        username=None,
        display_name="Agent",
    )
    assert (
        bot.answer_callback_query.await_args.kwargs["text"]
        == "Вы уже получили агента в этом наборе."
    )
    tracker.get_horoscope.assert_not_awaited()
    bot.send_message.assert_not_awaited()
