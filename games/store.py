"""In-memory storage for active group games."""

from __future__ import annotations

from collections.abc import MutableMapping

from .engine import GameError, GamePhase, GameState


class GameAlreadyActive(GameError):
    pass


class GameStore:
    """Keep at most one active game per chat."""

    def __init__(self, games: MutableMapping[int, GameState] | None = None) -> None:
        self._games = games if games is not None else {}

    def add(self, game: GameState) -> GameState:
        existing = self._games.get(game.chat_id)
        if existing is not None and existing.phase is not GamePhase.FINISHED:
            raise GameAlreadyActive(f"chat {game.chat_id} already has an active game")
        self._games[game.chat_id] = game
        return game

    def get(self, chat_id: int) -> GameState | None:
        return self._games.get(chat_id)

    def remove(
        self, chat_id: int, *, expected: GameState | None = None
    ) -> GameState | None:
        if expected is not None and self._games.get(chat_id) is not expected:
            return None
        return self._games.pop(chat_id, None)
