import json
import sys
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

sys.modules.setdefault(
    "config",
    SimpleNamespace(
        client=Mock(),
        logger=Mock(),
        TELEGRAM_MAX_MESSAGE_LENGTH=4096,
        tz=timezone.utc,
    ),
)

from handlers.ai import (
    build_ai_horoscope_blocks,
    build_tarot_blocks,
    _strip_markdown_wrappers,
)
from telegram_utils import TelegramAPIError, send_rich_message


def test_strip_markdown_wrappers_from_zodiac_title():
    assert _strip_markdown_wrappers("**Овен**") == "Овен"
    assert _strip_markdown_wrappers("__Телец__") == "Телец"
    assert _strip_markdown_wrappers("`Близнецы`") == "Близнецы"
    assert _strip_markdown_wrappers("**Овен**\nПервое предложение") == (
        "Овен\nПервое предложение"
    )


def test_strip_markdown_wrappers_preserves_plain_title():
    assert _strip_markdown_wrappers("Рак") == "Рак"


def test_build_ai_horoscope_blocks_structured():
    text = (
        "Отчёт по операции «Зодиак».\n"
        "Овен. День для смелых шагов.\n"
        "Телец. Спокойная работа без суеты.\n\n"
        "Bonne chance (Удачи)."
    )
    blocks = build_ai_horoscope_blocks(text)
    assert blocks[0] == {
        "type": "paragraph",
        "text": "Отчёт по операции «Зодиак».",
    }
    assert {
        "type": "details",
        "summary": "Овен",
        "blocks": [{"type": "paragraph", "text": "День для смелых шагов."}],
    } in blocks
    assert {
        "type": "details",
        "summary": "Телец",
        "blocks": [{"type": "paragraph", "text": "Спокойная работа без суеты."}],
    } in blocks
    assert blocks[-1] == {"type": "footer", "text": "Bonne chance (Удачи)."}


def test_build_ai_horoscope_blocks_puts_all_forecast_under_details():
    """Sign summary is only the name; first sentence stays inside details."""
    text = (
        "Шапка\n"
        "Овен\n"
        "Первое предложение про овна. Второе предложение."
    )
    blocks = build_ai_horoscope_blocks(text)
    assert {
        "type": "details",
        "summary": "Овен",
        "blocks": [
            {
                "type": "paragraph",
                "text": "Первое предложение про овна. Второе предложение.",
            }
        ],
    } in blocks
    assert not any(
        b.get("type") == "details" and "Первое" in str(b.get("summary", ""))
        for b in blocks
    )


def test_build_ai_horoscope_blocks_strips_markdown_titles():
    text = "Шапка\n**Овен**. Текст про овна."
    blocks = build_ai_horoscope_blocks(text)
    assert any(
        b.get("type") == "details"
        and b.get("summary") == "Овен"
        and b["blocks"][0]["text"] == "Текст про овна."
        for b in blocks
    )


def test_build_ai_horoscope_blocks_no_sections_uses_expandable():
    text = "Просто сплошной текст без знаков."
    assert build_ai_horoscope_blocks(text) == [
        {"type": "expandable_blockquote", "text": text}
    ]


@pytest.mark.asyncio
async def test_send_rich_message_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/botTEST:TOKEN/sendRichMessage")
        body = json.loads(request.content.decode())
        assert body["chat_id"] == 42
        assert body["rich_message"]["blocks"] == [{"type": "paragraph", "text": "hi"}]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    result = await send_rich_message(
        "TEST:TOKEN",
        42,
        [{"type": "paragraph", "text": "hi"}],
        transport=httpx.MockTransport(handler),
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_send_rich_message_accepts_inline_keyboard():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        assert body["reply_markup"] == {
            "inline_keyboard": [[{"text": "Open", "callback_data": "spy:menu:profile"}]]
        }
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    await send_rich_message(
        "TEST:TOKEN",
        42,
        [{"type": "paragraph", "text": "hi"}],
        reply_markup={
            "inline_keyboard": [[{"text": "Open", "callback_data": "spy:menu:profile"}]]
        },
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_send_rich_message_raises_on_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "description": "Bad Request: invalid blocks"}
        )

    with pytest.raises(TelegramAPIError, match="invalid blocks"):
        await send_rich_message(
            "T",
            1,
            [{"type": "paragraph", "text": "x"}],
            transport=httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_cycle_placeholder_rotates_frames():
    import asyncio
    from unittest.mock import AsyncMock

    from handlers.ai import _cycle_placeholder_text

    edits: list[str] = []

    class _Msg:
        async def edit_text(self, text, parse_mode=None):
            edits.append(text)

    stop = asyncio.Event()

    async def _stop_later():
        await asyncio.sleep(0.12)
        stop.set()

    context = SimpleNamespace(
        bot=SimpleNamespace(send_chat_action=AsyncMock())
    )
    await asyncio.gather(
        _cycle_placeholder_text(
            context,
            _Msg(),
            ("a", "b", "c"),
            stop,
            interval=0.04,
            log_prefix="test",
            chat_id=1,
        ),
        _stop_later(),
    )
    assert edits
    assert edits[0] == "b"
    assert set(edits) <= {"a", "b", "c"}


def test_build_tarot_blocks_structured():
    spread = [
        {
            "time": "прошлое",
            "card": "Шут",
            "orientation": "прямая",
        },
        {
            "time": "настоящее",
            "card": "Башня",
            "orientation": "перевернутая",
        },
        {
            "time": "будущее",
            "card": "Звезда",
            "orientation": "прямая",
        },
    ]
    text = (
        "Карты шепчут о дороге.\n"
        "Прошлое\n"
        "Ты уже сделал первый шаг.\n"
        "Настоящее\n"
        "Сейчас всё шатко, но живо.\n"
        "Будущее\n"
        "Впереди ясный ориентир.\n"
        "Итог\n"
        "Хаос ведёт к обновлению.\n"
        "Совет\n"
        "Держи курс и не жги мосты.\n\n"
        "La route continue (Дорога продолжается)."
    )
    blocks = build_tarot_blocks(spread, text)
    assert blocks[0]["type"] == "paragraph"
    assert "Шут" in blocks[0]["text"]
    assert blocks[1] == {"type": "paragraph", "text": "Карты шепчут о дороге."}
    assert {
        "type": "details",
        "summary": "Прошлое",
        "blocks": [{"type": "paragraph", "text": "Ты уже сделал первый шаг."}],
    } in blocks
    assert {
        "type": "details",
        "summary": "Совет",
        "blocks": [{"type": "paragraph", "text": "Держи курс и не жги мосты."}],
    } in blocks
    assert blocks[-1] == {
        "type": "footer",
        "text": "La route continue (Дорога продолжается).",
    }


def test_build_tarot_blocks_fallback_without_sections():
    spread = [{"time": "прошлое", "card": "Шут", "orientation": "прямая"}]
    blocks = build_tarot_blocks(spread, "Просто сплошной текст.")
    assert blocks[0]["type"] == "paragraph"
    assert {
        "type": "details",
        "summary": "Гадание",
        "blocks": [{"type": "paragraph", "text": "Просто сплошной текст."}],
    } in blocks
