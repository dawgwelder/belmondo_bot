"""Narrative text generation with a strict LLM boundary and local fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from config import logger
from games.llm import compact, request_json, untrusted_json_block

from .database import SQLiteDatabase
from .models import SpawnEvent
from .settings import SpySettings
from .persistence.narrative import NarrativeRepository

_RECRUITMENT_TEMPLATE_BODIES = (
    "У служебного входа замечен человек, который слишком старательно не смотрит по сторонам.",
    "В телефонной будке оставлен конверт без адреса. Такие письма долго не ждут.",
    "Связной перепутал условный знак и теперь ищет того, кто поймёт намёк первым.",
    "Гардеробщик придержал чужое пальто. Во внутреннем кармане кто-то оставил рекомендацию Центра.",
    "Посетитель библиотеки просит книгу, которой никогда не было в каталоге. Кажется, он знает нужный пароль.",
    "Таксист заглушил мотор у пустого подъезда. На пассажирском сиденье лежит аккуратно сложенная газета.",
    "На вокзале появился скрипач без футляра. Он узнаёт знакомых по отражению в витрине.",
    "Соседний столик занят человеком с двумя чашками кофе. Вторую он явно заказал не для себя.",
)
_HANDLER_TEMPLATE_BODIES = (
    "Куратор занял дальний столик и молча разложил на нём папки с новыми легендами.",
    "В неприметном кафе появился человек из Центра. Сегодня он готов укрепить вашу сеть.",
    "Старый связной открыл дипломат и ждёт тех, кому есть что предложить для обмена.",
    "За закрытой дверью ателье горит настольная лампа. Куратор примеряет легенды так же тщательно, как костюмы.",
    "В музейном гардеробе появился лишний номерок. Хозяин пальто распоряжается весьма необычным архивом.",
    "Пианист замолчал на середине мелодии. Человек у рояля достал из кармана печать Центра.",
    "Куратор выбрал для встречи пустую оранжерею. За стеклом его силуэт распадается на удобные алиби.",
    "На двери букиниста перевернули табличку. Последнему посетителю приготовили совсем не книгу.",
)
_DEAD_DROP_TEMPLATE_BODIES = (
    "Под скамейкой обнаружен контейнер с потёртой меткой Центра. Содержимое ещё можно забрать.",
    "В камере хранения осталась бесхозная ячейка. Код нацарапан прямо на жетоне.",
    "За водосточной трубой спрятан неприметный свёрток. Возможно, внутри есть что-то полезное.",
    "За кирпичом с белой царапиной нащупывается металлическая крышка. Стена хранит чужой секрет.",
    "Старый фотоаппарат в комиссионном магазине оказался тяжелее положенного. Под обшивкой спрятан контейнер.",
    "Почтовый ящик давно заколочен, но его дно недавно меняли. На свежем дереве остался след перчатки.",
    "В цветочном горшке вместо земли обнаружился свёрнутый брезент. Хозяйка лавки делает вид, что ничего не заметила.",
    "В театральной ложе забыли бинокль. Его футляр заперт на слишком серьёзный замок.",
)
_DEATH_OPERATION_TEMPLATE_BODIES = (
    "Центр открыл досье с чёрной печатью. Вернуться с этой операции удавалось немногим.",
    "На закрытом канале прозвучал приказ, после которого эфир сразу замолчал.",
    "На стол легла карта без маршрута отхода. Центр ждёт решение того, кто готов рискнуть сетью.",
    "Сейф открыли без свидетелей. Внутри лежит досье, которое никто не решался подписать.",
    "Лифт остановился на этаже, отсутствующем в плане здания. За дверями не слышно ни голосов, ни шагов.",
    "Курьер принёс пустой конверт с траурной каймой. В Центре прекрасно поняли намёк.",
    "На карте зачеркнули все знакомые адреса. Остался лишь дом, откуда перестали приходить донесения.",
    "Телефон зазвонил в опечатанном кабинете. Дежурный узнал голос человека из закрытого дела.",
)
_INTERCEPT_TEMPLATE_BODIES = (
    "Приёмник поймал короткую передачу на закрытой частоте. До смены канала осталось совсем немного.",
    "Среди радиопомех прозвучала условная фраза. Центр требует немедленной расшифровки.",
    "Перехваченный сигнал выглядит бессмысленным, но одна деталь выдаёт маршрут связного.",
    "Диктор сбился на слове, которого не было в тексте. Запись уже изучают в комнате прослушивания.",
    "На служебной ленте повторяется чужой позывной. Кто-то пытается спрятать передачу среди обычных сводок.",
    "Радиомастер заметил знакомый ритм под музыкальной заставкой. Дальнейший эфир он записывает молча.",
    "Антенна на крыше соседнего дома повернулась против ветра. Приёмник ответил сухим треском.",
    "В старом магнитофоне зашевелилась плёнка. Голос на записи старательно выдаёт себя за другого.",
)
_FIND_MOLE_TEMPLATE_BODIES = (
    "Четыре досье легли на стол одновременно. Одно из них принадлежит человеку Секции 7.",
    "Архив Вяземского восстановлен, но следы в нём ведут к одному из сотрудников сети.",
    "Центр собрал противоречивые показания. До закрытия дела осталось назвать крота.",
    "Подписи в служебном журнале выглядят безупречно. Именно это насторожило проверяющего.",
    "В архив вернули папку с чужой закладкой. Теперь каждое досье придётся прочесть заново.",
    "Совещание прервали, когда курьер положил на стол запечатанный пакет. Никто не захотел первым его вскрыть.",
    "Служебные объяснительные сложили рядом с уликами. Между аккуратными формулировками прячется чья-то ложь.",
    "Дверь переговорной закрыли изнутри. На столе остались только проверенные материалы расследования.",
)
_COOPERATIVE_TEMPLATE_BODIES = (
    "Центр разворачивает сеть наблюдения сразу в нескольких кварталах. Одному агенту периметр не удержать.",
    "Операция требует синхронной работы нескольких независимых ячеек разведсети.",
    "Цель появилась сразу на трёх камерах. Центр собирает общую группу сопровождения.",
    "На крыше погас условный фонарь. Посты наблюдения ждут согласованного сигнала из Центра.",
    "Связные заняли неприметные места вдоль набережной. Каждый видит лишь часть общего маршрута.",
    "В порту сменили охрану. Теперь разрозненные наблюдения нужно соединить в общую картину.",
    "Карта квартала разложена на столе диспетчера. Без подтверждения с соседних постов он не решается действовать.",
    "Служебная машина скрылась во дворах. Сеть наблюдения разворачивается вокруг последнего известного адреса.",
)
_CHASE_TEMPLATE_BODIES = (
    "Цель заметила хвост и растворяется в вечернем потоке. Центру нужны быстрые решения.",
    "Чёрный седан сорвался с места раньше сигнала. Маршрут отхода ещё можно перекрыть.",
    "Наблюдатель передал последнее направление цели и умолк. Погоня уже началась.",
    "Плащ мелькнул в дверях трамвая. Следующий поворот скроет его за плотным потоком машин.",
    "Мотоциклист бросил взгляд на витрину и резко сменил маршрут. Он заметил отражение преследователей.",
    "У театрального выхода хлопнула дверца такси. Водитель тронулся прежде, чем пассажир назвал адрес.",
    "Человек с портфелем смешался с пассажирами вокзала. Уходя, он старательно избегает освещённых проходов.",
    "Цель свернула под арку и оставила зонт у стены. Дождь отлично скрывает звук удаляющихся шагов.",
)
_NPC_TEMPLATE_BODIES = (
    "Редкий специалист Центра открыл временный канал и ждёт тех, кто готов предъявить ресурсы.",
    "В условленном месте появился куратор с доступом к закрытым программам подготовки.",
    "На служебной частоте объявлено короткое окно для особой сделки с Центром.",
    "Посетитель гостиницы назвался чужой фамилией. Портье без вопросов проводил его в служебную комнату.",
    "За кулисами кабаре появился неприметный дипломат. Его владелец явно приехал не на представление.",
    "В мастерскую принесли часы без стрелок. Для местного специалиста это давно знакомый знак.",
    "На пустой платформе стоит человек с запечатанным чемоданом. Встречающих он узнаёт без представления.",
    "В кабинете над книжной лавкой открыли ставни. Закрытый канал Центра снова доступен для связи.",
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
    @staticmethod
    def variants(event: SpawnEvent) -> tuple[str, ...]:
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
        return templates[index:] + templates[:index]

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        return EventNarrative(self.variants(event)[0], "template")


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
            "avoid_repeating": event.recent_narratives,
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
            "правила. Не повторяй формулировки и образы из avoid_repeating. Не добавляй Markdown и не создавай новых игровых сущностей. "
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
    """Rotate stored prose and refill it with bounded, optional LLM calls."""

    def __init__(
        self,
        database: SQLiteDatabase,
        primary: Narrator | None,
        *,
        cooldown_seconds=300,
        refresh_seconds=6 * 60 * 60,
        clock=None,
    ):
        self.database = database
        self.primary = primary
        self.repository = NarrativeRepository(cooldown_seconds, refresh_seconds)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()

    @staticmethod
    def context_key(event):
        # Legacy cache rows have unknown context and are not reused blindly.
        config_id = event.config_id
        if event.event_type == "find_mole":
            config_id = "investigation"  # Prose never contains generated case facts.
        payload = json.dumps(
            [config_id, event.story_hook, event.lore_context], ensure_ascii=False
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def narrate(self, event: SpawnEvent) -> EventNarrative:
        async with self._lock:
            context_key = self.context_key(event)
            now = self.clock()
            existing, recent, generate = await self.database.transaction(
                lambda connection: self.repository.prepare(
                    connection, event, context_key, now, self.primary is not None
                ),
                immediate=True,
            )
            if existing is not None:
                return EventNarrative(existing, "cache")
            body = None
            if generate:
                try:
                    narrative = await self.primary.narrate(
                        replace(event, recent_narratives=recent)
                    )
                    validated = (
                        LLMNarrator._validate({"body": narrative.body})
                        if narrative.source == "llm"
                        else None
                    )
                    if validated:
                        body = validated["body"]
                except Exception as error:
                    logger.warning(
                        "spy_narrator: generation failed event_id=%s reason=%s",
                        event.event_id,
                        type(error).__name__,
                    )
            result = await self.database.transaction(
                lambda connection: self.repository.finish(
                    connection,
                    event,
                    context_key,
                    self.clock(),
                    body,
                    TemplateNarrator.variants(event),
                ),
                immediate=True,
            )
            return EventNarrative(*result)


def build_narrator(
    settings: SpySettings, database: SQLiteDatabase | None = None
) -> Narrator:
    fallback = TemplateNarrator()
    primary = None
    if settings.llm_narrator_enabled:
        primary = ResilientNarrator(
            LLMNarrator(), fallback, settings.llm_narrator_timeout_seconds
        )
    if database is not None:
        return PersistentNarrator(
            database,
            primary,
            cooldown_seconds=settings.narrator_generation_cooldown_seconds,
            refresh_seconds=settings.narrator_refresh_seconds,
        )
    return primary or fallback
