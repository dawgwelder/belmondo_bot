"""Telegram-related helpers: streaming OpenAI responses and long-message splitting."""

import asyncio

from config import TELEGRAM_MAX_MESSAGE_LENGTH


async def parse_stream(stream):
    """Concatenate text content from an async OpenAI streaming response."""
    text = ""
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content is not None:
            text += chunk.choices[0].delta.content
    return text


async def send_long_message(
    bot,
    chat_id: int,
    text: str,
    parse_mode: str = None,
    reply_to_message_id: int = None,
    max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH,
) -> None:
    """Send a message, splitting it into multiple messages if it exceeds Telegram's limit."""
    if len(text) <= max_length:
        kwargs = {"chat_id": chat_id, "text": text}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        await bot.send_message(**kwargs)
        return

    chunks: list[str] = []
    current_chunk = ""

    for line in text.split("\n"):
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""

            if len(line) > max_length:
                for word in line.split(" "):
                    if len(word) > max_length:
                        if current_chunk:
                            chunks.append(current_chunk)
                            current_chunk = ""
                        for char_idx in range(0, len(word), max_length):
                            chunks.append(word[char_idx : char_idx + max_length])
                    elif len(current_chunk) + len(word) + 1 > max_length:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = word
                    else:
                        current_chunk = (
                            current_chunk + " " + word if current_chunk else word
                        )
            else:
                current_chunk = line
        else:
            current_chunk = current_chunk + "\n" + line if current_chunk else line

    if current_chunk:
        chunks.append(current_chunk)

    for i, chunk in enumerate(chunks):
        kwargs = {"chat_id": chat_id, "text": chunk}
        if parse_mode:
            kwargs["parse_mode"] = parse_mode
        if reply_to_message_id and i == 0:
            kwargs["reply_to_message_id"] = reply_to_message_id
        await bot.send_message(**kwargs)
        if i < len(chunks) - 1:
            await asyncio.sleep(0.1)
