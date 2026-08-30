"""Event selection independent from scheduling and Telegram rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DirectorDecision:
    event_type: str


class GameDirector(Protocol):
    def choose_event(self) -> DirectorDecision:
        ...


class RuleBasedDirector:
    """MVP director; registry can gain weighted events without handler changes."""

    def choose_event(self) -> DirectorDecision:
        return DirectorDecision(event_type="recruitment")
