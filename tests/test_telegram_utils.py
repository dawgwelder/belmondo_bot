import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from telegram.error import BadRequest, NetworkError

from time_utils import MOSCOW

sys.modules.setdefault(
    "config",
    SimpleNamespace(
        client=Mock(), logger=Mock(), TELEGRAM_MAX_MESSAGE_LENGTH=4096, tz=MOSCOW
    ),
)

from telegram_utils import send_long_message


PARSE_ERROR = (
    "Can't parse entities: can't find end of the entity starting at byte offset 3361"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("parse_mode", [None, "markdown"])
async def test_send_long_message_preserves_valid_text(parse_mode):
    bot = SimpleNamespace(send_message=AsyncMock())
    kwargs = dict(chat_id=42, text="\n*Сводка*\n\n", reply_to_message_id=10)

    await send_long_message(bot, **kwargs, parse_mode=parse_mode)

    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    bot.send_message.assert_awaited_once_with(**kwargs)


@pytest.mark.asyncio
async def test_send_long_message_retries_invalid_markdown_as_plain_text():
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[BadRequest(PARSE_ERROR), None])
    )
    kwargs = dict(chat_id=42, text="Сводка от user_name", reply_to_message_id=10)

    await send_long_message(bot, **kwargs, parse_mode="markdown")

    assert bot.send_message.await_args_list == [
        call(**kwargs, parse_mode="markdown"),
        call(**kwargs, parse_mode=None),
    ]


@pytest.mark.asyncio
async def test_send_long_message_retries_only_rejected_chunk(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("telegram_utils.asyncio.sleep", sleep)
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[None, BadRequest(PARSE_ERROR), None, None])
    )

    await send_long_message(
        bot,
        42,
        "*Первая*\nuser_name\n*Третья*",
        parse_mode="markdown",
        reply_to_message_id=10,
        max_length=10,
    )

    assert bot.send_message.await_args_list == [
        call(
            chat_id=42, text="*Первая*", parse_mode="markdown", reply_to_message_id=10
        ),
        call(chat_id=42, text="user_name", parse_mode="markdown"),
        call(chat_id=42, text="user_name", parse_mode=None),
        call(chat_id=42, text="*Третья*", parse_mode="markdown"),
    ]
    assert sleep.await_args_list == [call(0.1), call(0.1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error,parse_mode",
    [
        (BadRequest("Chat not found"), "markdown"),
        (NetworkError("Connection lost"), "markdown"),
        (BadRequest(PARSE_ERROR), None),
    ],
)
async def test_send_long_message_propagates_other_errors(error, parse_mode):
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))

    with pytest.raises(type(error)) as raised:
        await send_long_message(bot, 42, "Сводка", parse_mode=parse_mode)

    assert raised.value is error
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_long_message_propagates_failed_plain_text_retry():
    failure = BadRequest("Chat not found")
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[BadRequest(PARSE_ERROR), failure])
    )

    with pytest.raises(BadRequest) as raised:
        await send_long_message(bot, 42, "user_name", parse_mode="markdown")

    assert raised.value is failure
    assert bot.send_message.await_count == 2
