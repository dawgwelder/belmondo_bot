"""Spy Clicker Telegram event views."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from telegram import CallbackGame, InlineKeyboardButton, InlineKeyboardMarkup
from spy_game.models import RecruitmentProgress, SpawnEvent
from spy_game.narrator import EventNarrative
from spy_game.settings import (
    DEFAULT_HANDLER_RECIPES,
    DEFAULT_INTERCEPT_SCENARIOS,
    DEFAULT_MOLE_CASES,
    DEFAULT_NPC_RECIPES,
)
from .constants import RECRUITMENT_PROGRESS_MARKER


def _claim_keyboard(event_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Завербовать", callback_data=f"spy:claim:{event_id}")]]
    )


def _event_keyboard(
    event: SpawnEvent,
    recipes=DEFAULT_HANDLER_RECIPES,
    intercept_scenarios=DEFAULT_INTERCEPT_SCENARIOS,
    npc_recipes=DEFAULT_NPC_RECIPES,
    handler_reward_multiplier=2,
    npc_reward_multiplier=2,
    mole_cases=DEFAULT_MOLE_CASES,
):
    if event.event_type == "recruitment":
        return _claim_keyboard(event.event_id)
    if event.event_type == "dead_drop":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Обыскать тайник",
                        callback_data=f"spy:search:{event.event_id}",
                    )
                ]
            ]
        )
    if event.event_type == "death_operation" and event.config_id == "death_choice_v1":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Выбрать: all-in или личная миссия",
                        callback_data=f"spy:deathmenu:{event.event_id}",
                    )
                ]
            ]
        )
    if event.event_type == "death_operation":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Поставить сеть на кон",
                        callback_data=f"spy:death:{event.event_id}",
                    )
                ]
            ]
        )
    if event.event_type == "intercept":
        scenario = next(
            (
                candidate
                for candidate in intercept_scenarios
                if candidate.id == event.config_id
            ),
            None,
        )
        if scenario is None:
            raise ValueError(f"unknown intercept scenario: {event.config_id}")
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        option.display_name,
                        callback_data=f"spy:intercept_{option.id}:{event.event_id}",
                    )
                ]
                for option in scenario.options
            ]
        )
    if event.event_type == "find_mole":
        case = next(
            (candidate for candidate in mole_cases if candidate.id == event.config_id),
            None,
        )
        if case is None:
            raise ValueError(f"unknown mole case: {event.config_id}")
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"Обвинить: {suspect.codename}",
                        callback_data=f"spy:mole_{suspect.id}:{event.event_id}",
                    )
                ]
                for suspect in case.suspects
            ]
        )
    if event.event_type == "cooperative_operation":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Присоединиться к операции",
                        callback_data=f"spy:cooperate:{event.event_id}",
                    )
                ]
            ]
        )
    if event.event_type == "chase":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Начать преследование",
                        callback_data=f"spy:chase:{event.event_id}",
                    )
                ]
            ]
        )
    if event.event_type == "handler":
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{recipe.display_name} · ×{handler_reward_multiplier}",
                        callback_data=f"spy:exchange_{recipe.id}:{event.event_id}",
                    )
                ]
                for recipe in recipes
            ]
        )
    if event.event_type == "npc":
        available = [
            recipe for recipe in npc_recipes if recipe.npc_id == event.config_id
        ]
        if not available:
            raise ValueError(f"unknown NPC config: {event.config_id}")
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        f"{recipe.display_name} · ×{npc_reward_multiplier}",
                        callback_data=f"spy:npc_{recipe.id}:{event.event_id}",
                    )
                ]
                for recipe in available
            ]
        )
    raise ValueError(f"unsupported event type: {event.event_type}")


def _html5_game_keyboard(
    event_type: str, event_id: str | None = None
) -> InlineKeyboardMarkup:
    labels = {
        "dead_drop": "📦 Вскрыть тайник",
        "intercept": "📡 Настроить перехват",
        "find_mole": "🔎 Изучить досье",
        "death_operation": "🕵️ All-in или личная миссия",
    }
    label = labels[event_type]
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_game=CallbackGame(),
                )
            ],
            *(
                [
                    [
                        InlineKeyboardButton(
                            "Текстовый режим", callback_data=f"spy:deathmenu:{event_id}"
                        )
                    ]
                ]
                if event_type == "death_operation" and event_id
                else []
            ),
        ]
    )


def _recruitment_message_text(
    narrative_or_current_text: str,
    progress: RecruitmentProgress,
) -> str:
    if progress.completed:
        status = (
            "✅ Набор завершён."
            if progress.claims >= progress.required_claims
            else "⌛ Набор: время истекло."
        )
        lines = [status, f"Контакты: {progress.claims}/{progress.required_claims}"]
        if progress.usernames:
            lines.append("Подтверждены: " + ", ".join(progress.usernames))
        return "\n".join(lines)
    if RECRUITMENT_PROGRESS_MARKER in narrative_or_current_text:
        intro = narrative_or_current_text.split(RECRUITMENT_PROGRESS_MARKER, 1)[
            0
        ].rstrip()
    else:
        intro = (
            "🚨 СИГНАЛ РАЗВЕДСЕТИ\n\n"
            f"{narrative_or_current_text}\n\n"
            f"Первые {progress.required_claims} разных пользователя "
            "получат по агенту."
        )
    progress_lines = [
        RECRUITMENT_PROGRESS_MARKER,
        f"Контакты: {progress.claims}/{progress.required_claims}",
    ]
    if progress.usernames:
        progress_lines.append("Подтверждены: " + ", ".join(progress.usernames))
    progress_lines.append(
        f"Свободных контактов: {progress.required_claims - progress.claims}."
    )
    return f"{intro}\n\n" + "\n".join(progress_lines)


def build_event_blocks(
    event: SpawnEvent,
    narrative: EventNarrative,
    *,
    death_success_percent: int = 35,
    death_reward_multiplier: int = 2,
    intercept_prompt: str | None = None,
    cooperative_required: int = 3,
    recruitment_required: int = 3,
    handler_reward_multiplier: int = 2,
    npc_reward_multiplier: int = 2,
    mole_case=None,
) -> list[dict]:
    lifetime_minutes = max(
        1,
        math.ceil((event.expires_at - datetime.now(timezone.utc)).total_seconds() / 60),
    )
    is_handler = event.event_type == "handler"
    is_dead_drop = event.event_type == "dead_drop"
    is_death_operation = event.event_type == "death_operation"
    is_intercept = event.event_type == "intercept"
    is_cooperative = event.event_type == "cooperative_operation"
    is_chase = event.event_type == "chase"
    is_npc = event.event_type == "npc"
    is_mole = event.event_type == "find_mole"
    npc_titles = {
        "recruiter": "🧑‍💼 РЕКРУТЕР",
        "operations_chief": "🎖 НАЧАЛЬНИК ОПЕРАЦИЙ",
        "counterintelligence": "🔎 КОНТРРАЗВЕДКА",
    }
    return [
        {
            "type": "paragraph",
            "text": (
                "🔎 НАЙТИ КРОТА"
                if is_mole
                else "💀 СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ"
                if is_death_operation
                else "📡 ПЕРЕХВАТ"
                if is_intercept
                else "🤝 СОВМЕСТНАЯ ОПЕРАЦИЯ"
                if is_cooperative
                else "🏎 ПОГОНЯ"
                if is_chase
                else npc_titles.get(event.config_id, "🗝 СПЕЦИАЛЬНЫЙ КУРАТОР")
                if is_npc
                else "🗂 ВСТРЕЧА С КУРАТОРОМ"
                if is_handler
                else "📦 ТАЙНИК РАЗВЕДСЕТИ"
                if is_dead_drop
                else "🚨 СИГНАЛ РАЗВЕДСЕТИ"
            ),
        },
        {"type": "paragraph", "text": narrative.body},
        *(
            [
                {"type": "paragraph", "text": mole_case.briefing},
                {
                    "type": "details",
                    "summary": "Улики",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": "\n".join(
                                f"{index}. {clue}"
                                for index, clue in enumerate(mole_case.clues, 1)
                            ),
                        }
                    ],
                },
                {
                    "type": "details",
                    "summary": "Досье подозреваемых",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "text": (
                                f"{suspect.codename} · {suspect.role}\n"
                                f"{suspect.dossier}"
                            ),
                        }
                        for suspect in mole_case.suspects
                    ],
                },
            ]
            if is_mole and mole_case is not None
            else []
        ),
        *(
            [{"type": "paragraph", "text": intercept_prompt}]
            if is_intercept and intercept_prompt
            else []
        ),
        {
            "type": "footer",
            "text": (
                "Одна финальная версия на игрока. Первый верный ответ получает "
                "случайный предмет и 3 агентов Tier 1. "
                f"Окно: ~{lifetime_minutes} мин."
                if is_mole
                else f"Два нажатия для подтверждения. Шанс успеха {death_success_percent}%: "
                "провал заберёт всех агентов, успех вернёт состав "
                f"×{death_reward_multiplier} и даст Tier 3. "
                f"Окно: ~{lifetime_minutes} мин."
                if is_death_operation
                else "Первый ответ закроет канал. Верная расшифровка даст предмет. "
                f"Окно: ~{lifetime_minutes} мин."
                if is_intercept
                else f"Нужно {cooperative_required} разных участников. "
                "После достижения цели награду получит каждый. "
                f"Окно: ~{lifetime_minutes} мин."
                if is_cooperative
                else "Два этапа могут закрыть разные игроки. "
                f"Окно: ~{lifetime_minutes} мин."
                if is_chase
                else "Первый успешный обмен закрывает окно NPC. Бонус встречи: "
                f"результат ×{npc_reward_multiplier}. Окно: ~{lifetime_minutes} мин."
                if is_npc
                else "Первый успешный обмен закроет встречу. Бонус встречи: "
                f"результат ×{handler_reward_multiplier}. Окно: ~{lifetime_minutes} мин."
                if is_handler
                else f"Первый обыскавший забирает содержимое. Окно: ~{lifetime_minutes} мин."
                if is_dead_drop
                else f"Первые {recruitment_required} разных пользователя получат "
                f"по агенту. Окно: ~{lifetime_minutes} мин."
            ),
        },
    ]
