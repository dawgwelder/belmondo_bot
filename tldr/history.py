"""Fetch recent chat text messages via Telethon for /tldr."""

from __future__ import annotations

from typing import Any

from telethon import TelegramClient

from tldr.summarize import ChatMessage, filter_text_messages


def sender_display_name(sender: Any) -> str:
    if sender is None:
        return "Кто-то"
    title = getattr(sender, "title", None)
    if title:
        return str(title).strip() or "Кто-то"
    first = (getattr(sender, "first_name", None) or "").strip()
    last = (getattr(sender, "last_name", None) or "").strip()
    full = f"{first} {last}".strip()
    if full:
        return full
    username = (getattr(sender, "username", None) or "").strip()
    if username:
        return username
    return "Кто-то"


def is_bot_sender(sender: Any) -> bool:
    """True when Telethon sender is a User flagged as bot."""
    if sender is None:
        return False
    return bool(getattr(sender, "bot", False))


def message_to_chat_message(message: Any) -> ChatMessage | None:
    text = getattr(message, "text", None)
    if text is None or not str(text).strip():
        return None
    sender = getattr(message, "sender", None)
    return ChatMessage(
        message_id=int(getattr(message, "id")),
        sender_name=sender_display_name(sender),
        text=str(text),
        is_bot=is_bot_sender(sender),
    )


async def _create_client(config) -> TelegramClient:
    client = TelegramClient(
        str(config["auth"]["phone"]),
        config["auth"]["api_id"],
        config["auth"]["api_hash"],
    )
    await client.start()
    return client


async def fetch_chat_text_messages(
    chat_id: int,
    limit: int,
    *,
    exclude_message_id: int | None = None,
    config=None,
) -> list[ChatMessage]:
    """Load up to ``limit`` recent messages and return chronological text-only rows."""
    if config is None:
        from config import config as app_config

        config = app_config

    client = await _create_client(config)
    try:
        collected: list[ChatMessage] = []
        async for message in client.iter_messages(chat_id, limit=limit):
            sender = getattr(message, "sender", None)
            if sender is None:
                try:
                    sender = await message.get_sender()
                except Exception:
                    sender = None
            text = getattr(message, "text", None)
            if text is None or not str(text).strip():
                continue
            collected.append(
                ChatMessage(
                    message_id=int(message.id),
                    sender_name=sender_display_name(sender),
                    text=str(text),
                    is_bot=is_bot_sender(sender),
                )
            )
        collected.reverse()
        return filter_text_messages(
            collected, exclude_message_id=exclude_message_id
        )
    finally:
        await client.disconnect()
