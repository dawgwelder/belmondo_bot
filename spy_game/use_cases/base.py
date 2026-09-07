"""Runtime shared by application use cases; owns no extra connection or executor."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from ..database import SQLiteDatabase
from ..duels import SpyDuelRepository
from ..repositories import SpyRepository
from ..scheduler import RandomSource
from ..settings import SpySettings


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class UseCaseContext:
    settings: SpySettings
    database: SQLiteDatabase
    repository: SpyRepository
    duel_repository: SpyDuelRepository
    rng: RandomSource


class UseCases:
    def __init__(self, context: UseCaseContext) -> None:
        self.context = context

    @property
    def settings(self) -> SpySettings:
        return self.context.settings

    @property
    def database(self) -> SQLiteDatabase:
        return self.context.database

    @property
    def repository(self) -> SpyRepository:
        return self.context.repository

    @property
    def duel_repository(self) -> SpyDuelRepository:
        return self.context.duel_repository

    @property
    def rng(self) -> RandomSource:
        return self.context.rng

    def chat_is_available(self, chat_id: int) -> bool:
        return self.settings.enabled and self.settings.chat_is_allowed(chat_id)

    @staticmethod
    def _game_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
