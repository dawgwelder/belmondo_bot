"""Story-aware event selection with a strict optional LLM boundary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from games.llm import request_json, untrusted_json_block

from .models import DirectorState
from .scheduler import RandomSource
from .settings import SpySettings

TONES = ("serious", "paranoid", "bureaucratic", "absurd")
KNOWN_STORY_HOOKS = {"mole_hunt", "section_7", "colonel_vyazemsky"}
logger = logging.getLogger("Belmondo Logger")


@dataclass(frozen=True)
class DirectorDecision:
    event_type: str
    tone: str = "bureaucratic"
    story_hook: str | None = None
    intensity: int = 1


class GameDirector(Protocol):
    async def choose_event(self, state: DirectorState) -> DirectorDecision:
        ...


def selection_candidates(state: DirectorState) -> tuple[str, ...]:
    """Engine constraints shared by local and LLM directors."""
    allowed = tuple(
        event
        for event in state.allowed_events
        if event != "find_mole"
        or (state.story_arc == "mole_hunt" and state.story_stage >= 3)
    )
    recent = state.recent_events
    if (
        "recruitment" in allowed
        and len(recent) >= 3
        and "recruitment" not in recent[:3]
    ):
        return ("recruitment",)
    if (
        "find_mole" in allowed
        and state.story_stage == 3
        and "find_mole" not in recent[:3]
    ):
        return ("find_mole",)
    if len(recent) >= 2 and recent[0] == recent[1]:
        alternatives = tuple(event for event in allowed if event != recent[0])
        if alternatives:
            return alternatives
    return allowed


def selection_weights(
    settings: SpySettings, state: DirectorState
) -> tuple[tuple[str, int], ...]:
    candidates = selection_candidates(state)
    weighted = []
    for configured in settings.event_weights:
        event = configured.event_type
        if event not in candidates:
            continue
        weight = configured.weight
        if event == "cooperative_operation":
            weight = weight + 2 if state.active_players >= 3 else max(1, weight // 2)
        if state.story_arc == "mole_hunt" and (
            (state.story_stage == 1 and event == "cooperative_operation")
            or (state.story_stage == 2 and event == "handler")
        ):
            weight += 3
        weight = max(1, weight // (1 + state.recent_events[:5].count(event)))
        if state.recent_events and event == state.recent_events[0]:
            weight = max(1, weight // 3)
        weighted.append((event, weight))
    return tuple(weighted)


class RuleBasedDirector:
    """Weighted variety, a recruitment floor, and bounded story priorities."""

    def __init__(self, settings: SpySettings, rng: RandomSource) -> None:
        self.settings = settings
        self.rng = rng

    async def choose_event(self, state: DirectorState) -> DirectorDecision:
        weighted = selection_weights(self.settings, state)
        if not weighted:
            raise RuntimeError("director has no allowed events")
        roll = self.rng.randint(1, sum(weight for _, weight in weighted))
        cumulative = 0
        selected = weighted[-1][0]
        for event_type, weight in weighted:
            cumulative += weight
            if roll <= cumulative:
                selected = event_type
                break
        # Tone is a separate draw, not correlated with the selected event's weight.
        tone = TONES[self.rng.randint(0, len(TONES) - 1)]
        intensity = (
            3 if state.activity_score >= 30 else 2 if state.activity_score >= 15 else 1
        )
        story_hook = state.story_arc
        if story_hook is None and selected == "intercept":
            story_hook = "mole_hunt"
        return DirectorDecision(selected, tone, story_hook, intensity)


RequestJSON = Callable[..., Awaitable[dict[str, Any] | None]]


class LLMDirector:
    """Let the model select only validated values from an engine-owned snapshot."""

    def __init__(
        self,
        request: RequestJSON = request_json,
        timeout_seconds: float = 8,
        settings: SpySettings | None = None,
    ) -> None:
        self._request = request
        self.timeout_seconds = timeout_seconds
        self.settings = settings

    async def choose_event(self, state: DirectorState) -> DirectorDecision:
        snapshot = {
            "chat": {
                "activity_score": state.activity_score,
                "active_players": state.active_players,
                "minutes_since_last_event": state.minutes_since_last_event,
            },
            "recent_events": state.recent_events,
            "story": {"arc": state.story_arc, "stage": state.story_stage},
            "constraints": {
                "allowed_events": selection_candidates(state),
                "weights": dict(selection_weights(self.settings, state))
                if self.settings
                else {},
                "allowed_tones": TONES,
                "allowed_story_hooks": sorted(KNOWN_STORY_HOOKS),
                "intensity": {"minimum": 1, "maximum": 3},
            },
        }
        prompt = (
            "Ты AI Director шпионской Telegram-игры. Выбери только значения из "
            "переданных constraints. Не рассчитывай награды и не добавляй механику. "
            "Верни строго JSON с ключами event_type, tone, story_hook, intensity; "
            "story_hook может быть null. Избегай недавних повторов, учитывай weights: "
            "это относительные приоритеты сервера, а не награды.\n\n"
            f"{untrusted_json_block(snapshot)}"
        )
        payload = await asyncio.wait_for(
            self._request(
                prompt,
                lambda candidate: self._validate(candidate, state),
                corrective_hint="Используй только разрешённые enum и все четыре ключа.",
            ),
            timeout=self.timeout_seconds,
        )
        if self._validate(payload, state) is None:
            raise RuntimeError("LLM director returned no valid decision")
        return DirectorDecision(**payload)

    @staticmethod
    def _validate(
        payload: dict[str, Any],
        state: DirectorState,
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or set(payload) != {
            "event_type",
            "tone",
            "story_hook",
            "intensity",
        }:
            return None
        event_type = payload.get("event_type")
        tone = payload.get("tone")
        story_hook = payload.get("story_hook")
        intensity = payload.get("intensity")
        if (
            not isinstance(event_type, str)
            or event_type not in selection_candidates(state)
            or tone not in TONES
        ):
            return None
        if story_hook is not None and (
            not isinstance(story_hook, str) or story_hook not in KNOWN_STORY_HOOKS
        ):
            return None
        if not isinstance(intensity, int) or isinstance(intensity, bool):
            return None
        if not 1 <= intensity <= 3:
            return None
        return {
            "event_type": event_type,
            "tone": tone,
            "story_hook": story_hook,
            "intensity": intensity,
        }


class ResilientDirector:
    def __init__(self, primary: GameDirector, fallback: GameDirector) -> None:
        self.primary = primary
        self.fallback = fallback

    async def choose_event(self, state: DirectorState) -> DirectorDecision:
        try:
            return await self.primary.choose_event(state)
        except Exception as error:
            logger.warning(
                "spy_director: fallback chat_id=%s reason=%s",
                state.chat_id,
                type(error).__name__,
            )
            return await self.fallback.choose_event(state)


def build_director(settings: SpySettings, rng: RandomSource) -> GameDirector:
    fallback = RuleBasedDirector(settings, rng)
    if not settings.llm_director_enabled:
        return fallback
    return ResilientDirector(
        LLMDirector(
            timeout_seconds=settings.llm_director_timeout_seconds, settings=settings
        ),
        fallback,
    )
