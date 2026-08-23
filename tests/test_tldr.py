from types import SimpleNamespace

from tldr.history import message_to_chat_message, sender_display_name
from tldr.summarize import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    ChatMessage,
    build_tldr_messages,
    filter_text_messages,
    format_history_for_prompt,
    parse_limit,
)


def test_parse_limit_default_when_no_args():
    limit, error, clamped = parse_limit([])
    assert limit == DEFAULT_LIMIT
    assert error is None
    assert clamped is False


def test_parse_limit_default_when_args_none():
    limit, error, clamped = parse_limit(None)
    assert limit == DEFAULT_LIMIT
    assert error is None
    assert clamped is False


def test_parse_limit_accepts_valid_n():
    limit, error, clamped = parse_limit(["42"])
    assert limit == 42
    assert error is None
    assert clamped is False


def test_parse_limit_clamps_above_max():
    limit, error, clamped = parse_limit([str(MAX_LIMIT + 50)])
    assert limit == MAX_LIMIT
    assert error is None
    assert clamped is True


def test_parse_limit_rejects_non_integer():
    limit, error, clamped = parse_limit(["abc"])
    assert limit is None
    assert error is not None
    assert clamped is False


def test_parse_limit_rejects_below_one():
    limit, error, clamped = parse_limit(["0"])
    assert limit is None
    assert error is not None
    assert clamped is False


def test_filter_text_messages_excludes_command_and_empty():
    messages = [
        ChatMessage(1, "A", "hello"),
        ChatMessage(2, "B", ""),
        ChatMessage(3, "C", "   "),
        ChatMessage(4, "D", "/tldr 100"),
        ChatMessage(5, "E", "world"),
    ]
    result = filter_text_messages(messages, exclude_message_id=4)
    assert [(m.message_id, m.text) for m in result] == [(1, "hello"), (5, "world")]


def test_format_history_for_prompt_truncates_long_text():
    messages = [
        ChatMessage(1, "Иван", "коротко"),
        ChatMessage(2, "Артём", "x" * 100),
    ]
    text = format_history_for_prompt(messages, max_chars_per_message=10)
    assert "Иван: коротко" in text
    assert "Артём: " in text
    assert "x" * 100 not in text
    assert len(text.split("Артём: ", 1)[1].split("\n", 1)[0]) == 10


def test_build_tldr_messages_is_narrative_system_user():
    messages = build_tldr_messages("Иван: привет\nАртём: скидки")
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    system = messages[0]["content"].lower()
    assert "нарратив" in system or "сцен" in system or "по авторам" in system
    assert "Иван: привет" in messages[1]["content"]
    assert "Артём: скидки" in messages[1]["content"]


def test_sender_display_name_prefers_first_last_then_username():
    assert sender_display_name(SimpleNamespace(first_name="Инара", last_name="К", username="x")) == "Инара К"
    assert sender_display_name(SimpleNamespace(first_name="Иван", last_name=None, username="ivan")) == "Иван"
    assert sender_display_name(SimpleNamespace(first_name=None, last_name=None, username="ghost")) == "ghost"
    assert sender_display_name(None) == "Кто-то"
    assert sender_display_name(SimpleNamespace(title="Чат ботов")) == "Чат ботов"


def test_message_to_chat_message_maps_text_and_skips_empty():
    sender = SimpleNamespace(first_name="Артём", last_name=None, username=None, bot=False)
    msg = SimpleNamespace(id=10, text="скидки", sender=sender)
    mapped = message_to_chat_message(msg)
    assert mapped == ChatMessage(10, "Артём", "скидки", is_bot=False)
    assert message_to_chat_message(SimpleNamespace(id=11, text="  ", sender=sender)) is None
    assert message_to_chat_message(SimpleNamespace(id=12, text=None, sender=sender)) is None


def test_message_to_chat_message_marks_bots():
    bot_sender = SimpleNamespace(first_name="Бельмондо", last_name=None, username="bot", bot=True)
    mapped = message_to_chat_message(SimpleNamespace(id=20, text="сводка", sender=bot_sender))
    assert mapped is not None
    assert mapped.is_bot is True


def test_filter_text_messages_excludes_bots():
    messages = [
        ChatMessage(1, "Иван", "привет", is_bot=False),
        ChatMessage(2, "Бельмондо", "суммаризация...", is_bot=True),
        ChatMessage(3, "Артём", "скидки", is_bot=False),
    ]
    result = filter_text_messages(messages)
    assert [(m.message_id, m.sender_name) for m in result] == [
        (1, "Иван"),
        (3, "Артём"),
    ]


def test_is_bot_sender():
    from tldr.history import is_bot_sender

    assert is_bot_sender(SimpleNamespace(bot=True)) is True
    assert is_bot_sender(SimpleNamespace(bot=False)) is False
    assert is_bot_sender(None) is False
    assert is_bot_sender(SimpleNamespace(title="Channel")) is False
