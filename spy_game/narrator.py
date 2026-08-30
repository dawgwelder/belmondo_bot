"""Narrative text generation with a strict LLM boundary and local fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from config import logger
from games.llm import compact, request_json, untrusted_json_block

from .models import SpawnEvent
from .settings import SpySettings

_TEMPLATE_BODIES = (
    "У служебного входа замечен человек, который слишком старательно не смотрит по сторонам.",
    "В телефонной будке оставлен конверт без адреса. Такие письма долго не ждут.",
    "Связной перепутал условный знак и теперь ищет того, кто поймёт намёк первым.",
)
_TONES = ("paranoid", "bureaucratic", "absurd")
_FORBIDDEN_TERMS = (
    "награ",
    "кноп",
    "очк",
    "минут",
    "секунд",
    "выигр",
    "репутац",
    "уровень",
    "http",
)


@dataclass(frozen=True)
class EventNarrative:
    body: str
    source: str


class Narrator(Protocol):
    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        ...


class NarrationUnavailable(RuntimeError):
    pass


class TemplateNarrator:
    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        index = sum(event.event_id.encode("utf-8")) % len(_TEMPLATE_BODIES)
        return EventNarrative(_TEMPLATE_BODIES[index], "template")


RequestJSON = Callable[..., Awaitable[dict[str, Any] | None]]


class LLMNarrator:
    """Generate prose only; gameplay facts never come from the model."""

    def __init__(self, request: RequestJSON = request_json) -> None:
        self._request = request

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        tone = _TONES[sum(event.event_id.encode("utf-8")) % len(_TONES)]
        snapshot = {
            "event_type": event.event_type,
            "tone": tone,
            "constraints": {
                "language": "ru",
                "sentences": "1-3",
                "max_characters": 500,
            },
        }
        prompt = (
            "Ты Narrative Layer шпионской игры в Telegram. Напиши только короткую "
            "атмосферную завязку события в стиле французского шпионского фильма. "
            "Не упоминай механику, кнопку, награду, победителя, количество, время или "
            "правила. Не добавляй Markdown и не создавай новых игровых сущностей. "
            'Верни строго JSON вида {"body":"текст"}.\n\n'
            f"{untrusted_json_block(snapshot)}"
        )
        payload = await self._request(
            prompt,
            self._validate,
            corrective_hint=(
                'Нужен ровно один строковый ключ "body" без механики и форматирования.'
            ),
        )
        if payload is None:
            raise NarrationUnavailable("LLM returned no valid narrative")
        return EventNarrative(payload["body"], "llm")

    @staticmethod
    def _validate(payload: dict[str, Any]) -> dict[str, str] | None:
        if not isinstance(payload, dict) or set(payload) != {"body"}:
            return None
        body = payload.get("body")
        if not isinstance(body, str):
            return None
        body = compact(body, 500)
        lowered = body.lower()
        promises_agent = "получ" in lowered and "агент" in lowered
        if (
            len(body) < 20
            or promises_agent
            or any(term in lowered for term in _FORBIDDEN_TERMS)
        ):
            return None
        if any(character.isdigit() for character in body):
            return None
        if any(marker in body for marker in ("**", "__", "`")):
            return None
        return {"body": body}


class ResilientNarrator:
    def __init__(
        self,
        primary: Narrator,
        fallback: Narrator,
        timeout_seconds: float,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.timeout_seconds = timeout_seconds

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        try:
            return await asyncio.wait_for(
                self.primary.narrate(event),
                timeout=self.timeout_seconds,
            )
        except Exception as error:
            logger.warning(
                "spy_narrator: fallback event_id=%s reason=%s",
                event.event_id,
                type(error).__name__,
            )
            return await self.fallback.narrate(event)


def build_narrator(settings: SpySettings) -> Narrator:
    fallback = TemplateNarrator()
    if not settings.llm_narrator_enabled:
        return fallback
    return ResilientNarrator(
        LLMNarrator(),
        fallback,
        settings.llm_narrator_timeout_seconds,
    )
