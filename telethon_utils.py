"""Serialize users of the shared Telegram session file."""

import asyncio
from contextlib import asynccontextmanager

from telethon import TelegramClient


_client_lock = asyncio.Lock()


@asynccontextmanager
async def managed_client(config):
    async with _client_lock:
        client = TelegramClient(
            str(config["auth"]["phone"]),
            config["auth"]["api_id"],
            config["auth"]["api_hash"],
        )
        try:
            await client.start()
            yield client
        finally:
            cleanup = asyncio.ensure_future(client.disconnect())
            cancelled = False
            # Telethon shields its own cleanup; keep the session lock until it ends.
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError
