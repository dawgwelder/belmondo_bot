"""Narrative text generation with a strict LLM boundary and local fallback."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from config import logger
from games.llm import compact, request_json, untrusted_json_block

from .database import SQLiteDatabase
from .models import SpawnEvent
from .settings import SpySettings

_RECRUITMENT_TEMPLATE_BODIES = (
    "У служебного входа замечен человек, который слишком старательно не смотрит по сторонам.",
    "В телефонной будке оставлен конверт без адреса. Такие письма долго не ждут.",
    "Связной перепутал условный знак и теперь ищет того, кто поймёт намёк первым.",
)
_HANDLER_TEMPLATE_BODIES = (
    "Куратор занял дальний столик и молча разложил на нём папки с новыми легендами.",
    "В неприметном кафе появился человек из Центра. Сегодня он готов укрепить вашу сеть.",
    "Старый связной открыл дипломат и ждёт тех, кому есть что предложить для обмена.",
)
_DEAD_DROP_TEMPLATE_BODIES = (
    "Под скамейкой обнаружен контейнер с потёртой меткой Центра. Содержимое ещё можно забрать.",
    "В камере хранения осталась бесхозная ячейка. Код нацарапан прямо на жетоне.",
    "За водосточной трубой спрятан неприметный свёрток. Возможно, внутри есть что-то полезное.",
)
_DEATH_OPERATION_TEMPLATE_BODIES = (
    "Центр открыл досье с чёрной печатью. Вернуться с этой операции удавалось немногим.",
    "На закрытом канале прозвучал приказ, после которого эфир сразу замолчал.",
    "На стол легла карта без маршрута отхода. Центр ждёт решение того, кто готов рискнуть сетью.",
)
_INTERCEPT_TEMPLATE_BODIES = (
    "Приёмник поймал короткую передачу на закрытой частоте. До смены канала осталось совсем немного.",
    "Среди радиопомех прозвучала условная фраза. Центр требует немедленной расшифровки.",
    "Перехваченный сигнал выглядит бессмысленным, но одна деталь выдаёт маршрут связного.",
)
_FIND_MOLE_TEMPLATE_BODIES = (
    "Четыре досье легли на стол одновременно. Одно из них принадлежит человеку Секции 7.",
    "Архив Вяземского восстановлен, но следы в нём ведут к одному из сотрудников сети.",
    "Центр собрал противоречивые показания. До закрытия дела осталось назвать крота.",
)
_COOPERATIVE_TEMPLATE_BODIES = (
    "Центр разворачивает сеть наблюдения сразу в нескольких кварталах. Одному агенту периметр не удержать.",
    "Операция требует синхронной работы нескольких независимых ячеек разведсети.",
    "Цель появилась сразу на трёх камерах. Центр собирает общую группу сопровождения.",
)
_CHASE_TEMPLATE_BODIES = (
    "Цель заметила хвост и растворяется в вечернем потоке. Центру нужны быстрые решения.",
    "Чёрный седан сорвался с места раньше сигнала. Маршрут отхода ещё можно перекрыть.",
    "Наблюдатель передал последнее направление цели и умолк. Погоня уже началась.",
)
_NPC_TEMPLATE_BODIES = (
    "Редкий специалист Центра открыл временный канал и ждёт тех, кто готов предъявить ресурсы.",
    "В условленном месте появился куратор с доступом к закрытым программам подготовки.",
    "На служебной частоте объявлено короткое окно для особой сделки с Центром.",
)
_TONES = ("serious", "paranoid", "bureaucratic", "absurd")
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
        templates = {
            "handler": _HANDLER_TEMPLATE_BODIES,
            "dead_drop": _DEAD_DROP_TEMPLATE_BODIES,
            "death_operation": _DEATH_OPERATION_TEMPLATE_BODIES,
            "intercept": _INTERCEPT_TEMPLATE_BODIES,
            "find_mole": _FIND_MOLE_TEMPLATE_BODIES,
            "cooperative_operation": _COOPERATIVE_TEMPLATE_BODIES,
            "chase": _CHASE_TEMPLATE_BODIES,
            "npc": _NPC_TEMPLATE_BODIES,
        }.get(event.event_type, _RECRUITMENT_TEMPLATE_BODIES)
        index = (
            sum(event.event_id.encode("utf-8")) + sum(event.tone.encode("utf-8"))
        ) % len(templates)
        return EventNarrative(templates[index], "template")


RequestJSON = Callable[..., Awaitable[dict[str, Any] | None]]


class LLMNarrator:
    """Generate prose only; gameplay facts never come from the model."""

    def __init__(self, request: RequestJSON = request_json) -> None:
        self._request = request

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        snapshot = {
            "event_type": event.event_type,
            "tone": event.tone if event.tone in _TONES else "bureaucratic",
            "story_hook": event.story_hook,
            "lore": event.lore_context,
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


class PersistentNarrator:
    """Reuse validated LLM prose and persist new variants in SQLite."""

    def __init__(self, database: SQLiteDatabase, primary: Narrator) -> None:
        self.database = database
        self.primary = primary

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        cached = await self.database.transaction(
            lambda connection: self._take_cached(connection, event),
            immediate=True,
        )
        if cached is not None:
            return EventNarrative(cached, "cache")
        narrative = await self.primary.narrate(event)
        if narrative.source == "llm":
            await self.database.transaction(
                lambda connection: connection.execute(
                    """
                    INSERT INTO event_templates(event_type, tone, text)
                    VALUES (?, ?, ?)
                    """,
                    (event.event_type, event.tone, narrative.body),
                ),
                immediate=True,
            )
        return narrative

    @staticmethod
    def _take_cached(connection, event: SpawnEvent) -> str | None:
        row = connection.execute(
            """
            SELECT id, text FROM event_templates
            WHERE event_type = ? AND tone = ?
            ORDER BY usage_count, id
            LIMIT 1
            """,
            (event.event_type, event.tone),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE event_templates
            SET usage_count = usage_count + 1,
                last_used_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
            WHERE id = ?
            """,
            (row["id"],),
        )
        return row["text"]


def build_narrator(
    settings: SpySettings,
    database: SQLiteDatabase | None = None,
) -> Narrator:
    fallback = TemplateNarrator()
    if not settings.llm_narrator_enabled:
        return fallback
    narrator: Narrator = ResilientNarrator(
        LLMNarrator(),
        fallback,
        settings.llm_narrator_timeout_seconds,
    )
    return PersistentNarrator(database, narrator) if database is not None else narrator
