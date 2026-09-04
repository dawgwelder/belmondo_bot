"""Telegram adapter for the persistent Spy Clicker game."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

from telegram import CallbackGame, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import logger
from guards import pause
from spy_game.models import (
    AgentCost,
    AgencyStatus,
    ChaseStatus,
    ChatStatus,
    ClaimStatus,
    CooperativeStatus,
    DeadDropGameStatus,
    DeathOperationStatus,
    EconomyStatus,
    EquipmentStatus,
    Inventory,
    InterceptGameStatus,
    InterceptStatus,
    NpcStatus,
    Profile,
    RecruitmentProgress,
    SpawnEvent,
)
from spy_game.narrator import EventNarrative, Narrator, TemplateNarrator
from spy_game.scheduler import ActivityTriggerSettings
from spy_game.service import SpyGameService
from spy_game.settings import (
    AGENT_TYPES,
    DEFAULT_HANDLER_RECIPES,
    DEFAULT_INTERCEPT_SCENARIOS,
    DEFAULT_NPC_RECIPES,
    ITEM_TYPES,
)
from telegram_utils import send_rich_message

SPY_CALLBACK_PATTERN = r"^spy:[a-z0-9_]{1,32}:[a-z0-9_]{1,24}$"
SPY_HTML5_GAME_PATTERN = r"^[A-Za-z0-9_]{1,64}$"
RECRUITMENT_PROGRESS_MARKER = "📡 ПРОГРЕСС НАБОРА"
ACTIVITY_PROFILE_LABELS = {
    "calm": "спокойный",
    "balanced": "сбалансированный",
    "aggressive": "агрессивный",
}
ACTIVITY_PROFILE_ALIASES = {
    "calm": "calm",
    "balanced": "balanced",
    "aggressive": "aggressive",
    "редко": "calm",
    "норма": "balanced",
    "часто": "aggressive",
}


def _service(context: ContextTypes.DEFAULT_TYPE) -> SpyGameService:
    service = context.bot_data.get("spy_game")
    if not isinstance(service, SpyGameService):
        raise RuntimeError("Spy Game service is unavailable")
    return service


def _narrator(context: ContextTypes.DEFAULT_TYPE) -> Narrator:
    narrator = context.bot_data.get("spy_narrator")
    return narrator if narrator is not None else TemplateNarrator()


def _markup_payload(markup: InlineKeyboardMarkup | None) -> dict | None:
    if markup is None:
        return None
    return markup.to_dict()


async def _send_rich(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    blocks: list[dict],
    *,
    fallback_text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> int:
    try:
        payload = await send_rich_message(
            context.bot.token,
            chat_id,
            blocks,
            reply_markup=_markup_payload(reply_markup),
        )
        return int(payload["result"]["message_id"])
    except Exception:
        logger.exception("spy_game: sendRichMessage failed chat_id=%s", chat_id)
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
            reply_markup=reply_markup,
        )
        return message.message_id


def _menu_keyboard(
    profile: Profile,
    webapp_url: str | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    if webapp_url:
        rows.append(
            [InlineKeyboardButton("🗄 Открыть оперативный центр", url=webapp_url)]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🪪 Досье", callback_data="spy:menu:profile"),
                InlineKeyboardButton("🕵️ Агенты", callback_data="spy:menu:agents"),
            ],
            [
                InlineKeyboardButton("🎒 Инвентарь", callback_data="spy:menu:inventory"),
                InlineKeyboardButton("🏆 Рейтинг", callback_data="spy:menu:leaderboard"),
            ],
            [
                InlineKeyboardButton(
                    "⏱ Состояние сети", callback_data="spy:menu:status"
                ),
                InlineKeyboardButton("🔄 Обновить", callback_data="spy:menu:refresh"),
            ],
            [
                InlineKeyboardButton(
                    "⭐ Повысить репутацию",
                    callback_data=f"spy:prestige:{profile.reputation}",
                ),
                InlineKeyboardButton(
                    "🏛 Своя служба",
                    callback_data="spy:agency:status",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🤝 Контакты Центра",
                    callback_data="spy:menu:contacts",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _claim_keyboard(event_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Завербовать", callback_data=f"spy:claim:{event_id}")]]
    )


def _event_keyboard(
    event: SpawnEvent,
    recipes=DEFAULT_HANDLER_RECIPES,
    intercept_scenarios=DEFAULT_INTERCEPT_SCENARIOS,
    npc_recipes=DEFAULT_NPC_RECIPES,
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
                        recipe.display_name,
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
                        recipe.display_name,
                        callback_data=f"spy:npc_{recipe.id}:{event.event_id}",
                    )
                ]
                for recipe in available
            ]
        )
    raise ValueError(f"unsupported event type: {event.event_type}")


def _display_name(user) -> str:
    return user.full_name or (f"@{user.username}" if user.username else str(user.id))


def _public_username(user) -> str | None:
    if not user.username or not user.username.strip("@"):
        return None
    return f"@{user.username.lstrip('@')}"


def _html5_game_keyboard(event_type: str) -> InlineKeyboardMarkup:
    label = "📦 Вскрыть тайник" if event_type == "dead_drop" else "📡 Настроить перехват"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    label,
                    callback_game=CallbackGame(),
                )
            ]
        ]
    )


def _public_label(user) -> str:
    return _public_username(user) or "Скрытый агент"


def _recruitment_message_text(
    narrative_or_current_text: str,
    progress: RecruitmentProgress,
) -> str:
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
    if progress.completed:
        progress_lines.append("✅ Набор завершён.")
    else:
        progress_lines.append(
            f"Свободных контактов: {progress.required_claims - progress.claims}."
        )
    return f"{intro}\n\n" + "\n".join(progress_lines)


async def _edit_recruitment_progress(
    context: ContextTypes.DEFAULT_TYPE,
    query,
    service: SpyGameService,
    event_id: str,
) -> None:
    locks = context.bot_data.setdefault("spy_recruitment_edit_locks", {})
    lock = locks.setdefault(event_id, asyncio.Lock())
    async with lock:
        progress = await service.get_recruitment_progress(event_id)
        if progress is None:
            return
        current_text = getattr(getattr(query, "message", None), "text", None)
        source = current_text or "Обнаружены потенциальные связные."
        reply_markup = None if progress.completed else _claim_keyboard(event_id)
        try:
            await query.edit_message_text(
                text=_recruitment_message_text(source, progress),
                reply_markup=reply_markup,
            )
        except Exception:
            logger.warning(
                "spy_game: recruitment progress edit failed event_id=%s",
                event_id,
            )
            if progress.completed:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    logger.warning(
                        "spy_game: recruitment keyboard remained event_id=%s",
                        event_id,
                    )


def _countdown(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "таймер ещё не назначен"
    seconds = (target - now).total_seconds()
    if seconds <= 0:
        return "событие ожидается в ближайший цикл"
    minutes = max(1, math.ceil(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"примерно через {hours} ч {minutes} мин"
    return f"примерно через {minutes} мин"


def _format_costs(costs: tuple[AgentCost, ...]) -> str:
    return ", ".join(
        f"{AGENT_TYPES[cost.agent_type].display_name} ×{cost.amount}" for cost in costs
    )


def _format_item_costs(costs) -> str:
    return ", ".join(
        f"{ITEM_TYPES[cost.item_type].display_name} ×{cost.amount}" for cost in costs
    )


def _format_drop_reward(reward) -> str:
    registry = AGENT_TYPES if reward.reward_type == "agent" else ITEM_TYPES
    definition = registry[reward.reward_id]
    return f"{definition.emoji} {definition.display_name} ×{reward.amount}"


def build_contact_blocks(recipes) -> list[dict]:
    names = {
        "operations_chief": "🎖 Начальник операций",
        "counterintelligence": "🔎 Контрразведка",
    }
    blocks: list[dict] = [{"type": "paragraph", "text": "🤝 ПОСТОЯННЫЕ КОНТАКТЫ ЦЕНТРА"}]
    for npc_id, title in names.items():
        lines = []
        for recipe in recipes:
            if recipe.npc_id != npc_id:
                continue
            costs = [
                part
                for part in (
                    _format_costs(recipe.agent_costs),
                    _format_item_costs(recipe.item_costs),
                )
                if part
            ]
            lines.append(
                f"{recipe.display_name}: {'; '.join(costs)} → "
                f"{_format_drop_reward(recipe.rewards[0])}"
            )
        if lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": title,
                    "blocks": [{"type": "paragraph", "text": "\n".join(lines)}],
                }
            )
    blocks.append(
        {
            "type": "footer",
            "text": (
                "Эти сделки доступны постоянно. Рекрутер по-прежнему появляется "
                "только как редкое событие."
            ),
        }
    )
    return blocks


def _contact_keyboard(recipes) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    recipe.display_name,
                    callback_data=f"spy:contact_{recipe.id}:0",
                )
            ]
            for recipe in recipes
        ]
    )


def build_menu_blocks(profile: Profile, status, now: datetime) -> list[dict]:
    if status.active_event_id:
        event_line = "В чате уже идёт операция — ищите сообщение с кнопкой."
    elif status.enabled:
        event_line = "Сигнал может прийти на пике, по инерции или случайно."
    else:
        event_line = "В этом чате сеть пока не активирована."
    return [
        {"type": "paragraph", "text": "🕵️ SPY CLICKER · ОПЕРАТИВНЫЙ ЦЕНТР"},
        {
            "type": "details",
            "summary": "Ваше досье",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Агентов: {profile.total_agents}\n"
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}"
                    ),
                }
            ],
        },
        {
            "type": "paragraph",
            "text": (f"Активность чата: {status.activity_score:.1f}\n{event_line}"),
        },
        {
            "type": "footer",
            "text": "Пик срабатывает сразу; инерция живёт недолго после беседы.",
        },
    ]


def build_profile_blocks(profile: Profile) -> list[dict]:
    name = (
        f"@{profile.username.lstrip('@')}"
        if profile.username and profile.username.strip("@")
        else "СКРЫТЫЙ АГЕНТ"
    )
    return [
        {"type": "paragraph", "text": f"🪪 ДОСЬЕ · {name}"},
        {
            "type": "details",
            "summary": "Показатели",
            "blocks": [
                {
                    "type": "paragraph",
                    "text": (
                        f"Репутация: {profile.reputation}\n"
                        f"Уровень службы: {profile.agency_level}\n"
                        f"Агентов в сети: {profile.total_agents}"
                    ),
                }
            ],
        },
        {"type": "footer", "text": "Никаких лишних имён. Только результаты."},
    ]


def build_agents_blocks(holdings) -> list[dict]:
    by_tier: dict[int, list[str]] = {}
    for holding in holdings:
        agent = AGENT_TYPES.get(holding.agent_type)
        if agent is None:
            continue
        by_tier.setdefault(agent.tier, []).append(
            f"{agent.emoji} {agent.display_name}: {holding.amount}"
        )
    blocks: list[dict] = [{"type": "paragraph", "text": "🕵️ АГЕНТУРНАЯ СЕТЬ"}]
    if not by_tier:
        blocks.append(
            {
                "type": "paragraph",
                "text": "Сеть пока пуста. Первым замечайте события в чате.",
            }
        )
    else:
        for tier in sorted(by_tier):
            blocks.append(
                {
                    "type": "details",
                    "summary": f"Уровень {tier}",
                    "blocks": [{"type": "paragraph", "text": "\n".join(by_tier[tier])}],
                }
            )
    blocks.append(
        {"type": "footer", "text": "Обычные агенты хранятся общим количеством."}
    )
    return blocks


def build_inventory_blocks(inventory: Inventory) -> list[dict]:
    equipped_by_type = {item.item_type: item.slot for item in inventory.equipped}
    blocks: list[dict] = [{"type": "paragraph", "text": "🎒 ИНВЕНТАРЬ"}]
    if not inventory.items:
        blocks.append(
            {
                "type": "paragraph",
                "text": "Инвентарь пуст. Ищите тайники разведсети.",
            }
        )
    else:
        equipment_lines = []
        consumable_lines = []
        for holding in inventory.items:
            item = ITEM_TYPES.get(holding.item_type)
            if item is None:
                continue
            suffix = (
                f" · слот {equipped_by_type[item.id]}"
                if item.id in equipped_by_type
                else ""
            )
            line = f"{item.emoji} {item.display_name}: {holding.amount}{suffix}"
            target = (
                equipment_lines
                if item.category.value == "equipment"
                else consumable_lines
            )
            target.append(line)
        if equipment_lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": "Экипировка",
                    "blocks": [
                        {"type": "paragraph", "text": "\n".join(equipment_lines)}
                    ],
                }
            )
        if consumable_lines:
            blocks.append(
                {
                    "type": "details",
                    "summary": "Расходные материалы",
                    "blocks": [
                        {"type": "paragraph", "text": "\n".join(consumable_lines)}
                    ],
                }
            )
    blocks.append(
        {
            "type": "footer",
            "text": (
                f"Занято слотов: {len(inventory.equipped)}/{inventory.slot_count}. "
                "Прослушка может усилить награду Recruitment."
            ),
        }
    )
    return blocks


def _inventory_keyboard(inventory: Inventory) -> InlineKeyboardMarkup | None:
    equipped_types = {item.item_type for item in inventory.equipped}
    rows = []
    for holding in inventory.items:
        item = ITEM_TYPES.get(holding.item_type)
        if (
            item is not None
            and item.category.value == "equipment"
            and item.id not in equipped_types
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        f"Надеть: {item.display_name}",
                        callback_data=f"spy:equip:{item.id}",
                    )
                ]
            )
    for equipped in inventory.equipped:
        item = ITEM_TYPES[equipped.item_type]
        rows.append(
            [
                InlineKeyboardButton(
                    f"Снять: {item.display_name}",
                    callback_data=f"spy:unequip:{equipped.slot}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def build_status_blocks(status, now: datetime) -> list[dict]:
    if status.active_event_id:
        state = f"Активная операция: {status.active_event_id}"
        timer = f"Окно закроется: {_countdown(status.active_event_expires_at, now)}"
    elif status.enabled:
        state = "Сеть активна"
        timer = "Триггеры: пик · инерция · случайный сигнал"
    else:
        state = "Сеть не активирована в этом чате"
        timer = "Таймер остановлен"
    blocks = [
        {"type": "paragraph", "text": "⏱ СОСТОЯНИЕ СЕТИ"},
        {
            "type": "paragraph",
            "text": f"{state}\nАктивность: {status.activity_score:.1f}\n{timer}",
        },
    ]
    if status.story_arc:
        blocks.append(
            {
                "type": "details",
                "summary": f"Сюжет: {status.story_arc} · этап {status.story_stage}",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": status.story_summary or "Сводка ещё не сформирована.",
                    }
                ],
            }
        )
    blocks.append(
        {
            "type": "footer",
            "text": "Порог запуска — 6 очков; со временем активность затухает.",
        }
    )
    return blocks


def build_leaderboard_blocks(entries) -> list[dict]:
    lines = [
        (
            f"{entry.rank}. {entry.display_name} — сеть {entry.total_agents}, "
            f"Tier 3: {entry.rare_agents}, репутация {entry.reputation}, "
            f"служба {entry.agency_level}"
        )
        for entry in entries
    ]
    return [
        {"type": "paragraph", "text": "🏆 РЕЙТИНГ РАЗВЕДСЕТЕЙ"},
        {
            "type": "paragraph",
            "text": "\n".join(lines) if lines else "Пока ни одно досье не открыто.",
        },
        {
            "type": "footer",
            "text": "Порядок: уровень службы, репутация, Tier 3, общий состав.",
        },
    ]


def build_event_blocks(
    event: SpawnEvent,
    narrative: EventNarrative,
    *,
    death_success_percent: int = 35,
    death_reward_multiplier: int = 2,
    intercept_prompt: str | None = None,
    cooperative_required: int = 3,
    recruitment_required: int = 3,
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
    npc_titles = {
        "recruiter": "🧑‍💼 РЕКРУТЕР",
        "operations_chief": "🎖 НАЧАЛЬНИК ОПЕРАЦИЙ",
        "counterintelligence": "🔎 КОНТРРАЗВЕДКА",
    }
    return [
        {
            "type": "paragraph",
            "text": (
                "💀 СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ"
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
            [{"type": "paragraph", "text": intercept_prompt}]
            if is_intercept and intercept_prompt
            else []
        ),
        {
            "type": "footer",
            "text": (
                f"Два нажатия для подтверждения. Шанс успеха {death_success_percent}%: "
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
                else "Первый успешный обмен закрывает окно NPC; стоимость и "
                f"результат определяет сервер. Окно: ~{lifetime_minutes} мин."
                if is_npc
                else f"Первый успешный обмен закроет встречу. Окно: ~{lifetime_minutes} мин."
                if is_handler
                else f"Первый обыскавший забирает содержимое. Окно: ~{lifetime_minutes} мин."
                if is_dead_drop
                else f"Первые {recruitment_required} разных пользователя получат "
                f"по агенту. Окно: ~{lifetime_minutes} мин."
            ),
        },
    ]


async def _profile_for_update(update: Update, service: SpyGameService) -> Profile:
    user = update.effective_user
    return await service.get_profile(
        user_id=user.id,
        username=user.username,
        display_name=_display_name(user),
    )


async def _send_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    profile = await _profile_for_update(update, service)
    status = await service.get_chat_status(update.effective_chat.id)
    webapp = context.bot_data.get("spy_webapp")
    launch_url = (
        webapp.launch_url(update.effective_chat.id, update.effective_user.id)
        if webapp is not None
        else None
    )
    now = datetime.now(timezone.utc)
    await _send_rich(
        context,
        update.effective_chat.id,
        build_menu_blocks(profile, status, now),
        fallback_text=(
            "🕵️ Spy Clicker\n"
            f"Агентов: {profile.total_agents}\n"
            f"Активность: {status.activity_score:.1f}\n"
            "Сигнал может прийти на пике, по инерции или случайно."
        ),
        reply_markup=_menu_keyboard(profile, launch_url),
    )


@pause
async def spy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    await _send_menu(update, context)


async def publish_spy_event(
    context: ContextTypes.DEFAULT_TYPE,
    event: SpawnEvent,
) -> int:
    service = context.bot_data.get("spy_game")
    recipes = (
        service.settings.handler_recipes
        if isinstance(service, SpyGameService)
        else DEFAULT_HANDLER_RECIPES
    )
    is_handler = event.event_type == "handler"
    is_dead_drop = event.event_type == "dead_drop"
    is_death_operation = event.event_type == "death_operation"
    is_intercept = event.event_type == "intercept"
    is_cooperative = event.event_type == "cooperative_operation"
    is_chase = event.event_type == "chase"
    is_npc = event.event_type == "npc"
    death_success_percent = (
        service.settings.death_operation_success_percent
        if isinstance(service, SpyGameService)
        else 35
    )
    death_reward_multiplier = (
        service.settings.death_operation_reward_multiplier
        if isinstance(service, SpyGameService)
        else 2
    )
    scenario = (
        service.settings.intercept_scenario(event.config_id or "")
        if isinstance(service, SpyGameService)
        else next(
            (
                candidate
                for candidate in DEFAULT_INTERCEPT_SCENARIOS
                if candidate.id == event.config_id
            ),
            None,
        )
    )
    cooperative_required = (
        service.settings.cooperative_required_contributions
        if isinstance(service, SpyGameService)
        else 3
    )
    recruitment_required = (
        service.settings.recruitment_winner_count
        if isinstance(service, SpyGameService)
        else 3
    )
    webapp = context.bot_data.get("spy_webapp")
    if (is_dead_drop or (is_intercept and scenario is not None)) and getattr(
        webapp, "game_enabled", False
    ):
        try:
            message = await context.bot.send_game(
                chat_id=event.chat_id,
                game_short_name=webapp.settings.game_short_name,
                reply_markup=_html5_game_keyboard(event.event_type),
            )
            logger.info(
                "spy_narrative_selected event_id=%s event_type=%s source=html5_game",
                event.event_id,
                event.event_type,
            )
            return message.message_id
        except Exception:
            logger.exception(
                "spy_game: HTML5 game publication failed, using fallback "
                "event_id=%s event_type=%s",
                event.event_id,
                event.event_type,
            )
    narrative = await _narrator(context).narrate(event)
    if event.event_type == "recruitment":
        progress = RecruitmentProgress(
            event_id=event.event_id,
            claims=0,
            required_claims=recruitment_required,
        )
        message = await context.bot.send_message(
            chat_id=event.chat_id,
            text=_recruitment_message_text(narrative.body, progress),
            reply_markup=_claim_keyboard(event.event_id),
        )
        logger.info(
            "spy_narrative_selected event_id=%s source=%s",
            event.event_id,
            narrative.source,
        )
        return message.message_id
    message_id = await _send_rich(
        context,
        event.chat_id,
        build_event_blocks(
            event,
            narrative,
            death_success_percent=death_success_percent,
            death_reward_multiplier=death_reward_multiplier,
            intercept_prompt=scenario.prompt if scenario else None,
            cooperative_required=cooperative_required,
            recruitment_required=recruitment_required,
        ),
        fallback_text=(
            "💀 Смертельная операция\n"
            f"Поставьте всех агентов: {death_success_percent}% на возврат состава "
            f"×{death_reward_multiplier} и бонус Tier 3."
            if is_death_operation
            else "📡 Перехват\nВыберите верную расшифровку сигнала."
            if is_intercept
            else f"🤝 Совместная операция\nНужно участников: {cooperative_required}."
            if is_cooperative
            else "🏎 Погоня\nНачните преследование, затем перехватите цель."
            if is_chase
            else "🗝 Специальный куратор\nПредъявите ресурсы для закрытой сделки."
            if is_npc
            else "🗂 Встреча с куратором\nПредъявите ресурсы для обмена."
            if is_handler
            else "📦 Тайник разведсети\nПервый обыскавший забирает содержимое."
            if is_dead_drop
            else "🚨 Сигнал разведсети\n"
            f"Замечены потенциальные связные. Первые {recruitment_required} "
            "разных пользователя получают агентов."
        ),
        reply_markup=_event_keyboard(
            event,
            recipes,
            (
                service.settings.intercept_scenarios
                if isinstance(service, SpyGameService)
                else DEFAULT_INTERCEPT_SCENARIOS
            ),
            (
                service.settings.npc_recipes
                if isinstance(service, SpyGameService)
                else DEFAULT_NPC_RECIPES
            ),
        ),
    )
    logger.info(
        "spy_narrative_selected event_id=%s source=%s",
        event.event_id,
        narrative.source,
    )
    return message_id


async def spy_html5_game_launch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    message = query.message if query is not None else None
    webapp = context.bot_data.get("spy_webapp")
    if query is None:
        return
    if user is None or chat is None or message is None:
        await query.answer(
            "Запуск из inline-сообщения пока не поддерживается.",
            show_alert=True,
        )
        return
    if (
        webapp is None
        or not getattr(webapp, "game_enabled", False)
        or query.game_short_name != webapp.settings.game_short_name
    ):
        await query.answer(
            "HTML5-операция временно недоступна. Используйте текстовый вариант.",
            show_alert=True,
        )
        return
    try:
        service = _service(context)
        result = await service.start_intercept_game(
            chat_id=chat.id,
            message_id=message.message_id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
        if result.status is InterceptGameStatus.NOT_FOUND:
            result = await service.start_dead_drop_game(
                chat_id=chat.id,
                message_id=message.message_id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
    except Exception:
        logger.exception(
            "spy_game: failed to start HTML5 operation chat_id=%s message_id=%s",
            chat.id,
            message.message_id,
        )
        await query.answer("Не удалось открыть операцию.", show_alert=True)
        return
    if result.status in {InterceptGameStatus.READY, DeadDropGameStatus.READY}:
        launch_url = webapp.game_launch_url(result.launch_token)
        if launch_url:
            await query.answer(url=launch_url)
            return
    if isinstance(result.status, DeadDropGameStatus):
        messages = {
            DeadDropGameStatus.ALREADY_PLAYED: "Вы уже использовали попытку.",
            DeadDropGameStatus.ALREADY_RESOLVED: "Тайник уже вскрыт.",
            DeadDropGameStatus.EXPIRED: "Тайник уже изъят Центром.",
            DeadDropGameStatus.DISABLED: "Разведсеть сейчас отключена.",
        }
        fallback = "Этот тайник больше недоступен."
    else:
        messages = {
            InterceptGameStatus.ALREADY_PLAYED: "Вы уже использовали попытку.",
            InterceptGameStatus.ALREADY_RESOLVED: "Канал уже перехвачен.",
            InterceptGameStatus.EXPIRED: "Канал уже замолчал.",
            InterceptGameStatus.DISABLED: "Разведсеть сейчас отключена.",
        }
        fallback = "Этот сигнал больше недоступен."
    await query.answer(
        messages.get(result.status, fallback),
        show_alert=True,
    )


async def _remove_event_keyboard(context, chat_id: int, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    except Exception:
        logger.warning(
            "spy_game: failed to remove keyboard chat_id=%s message_id=%s",
            chat_id,
            message_id,
        )


async def spy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    if query is None or user is None or chat is None:
        return
    parts = (query.data or "").split(":")
    if len(parts) != 3 or parts[0] != "spy":
        await query.answer("Некорректный сигнал.", show_alert=True)
        return
    service = _service(context)
    category, value = parts[1], parts[2]

    if category == "menu":
        if value not in {
            "refresh",
            "profile",
            "agents",
            "inventory",
            "contacts",
            "leaderboard",
            "status",
        }:
            await query.answer("Неизвестный пункт меню.", show_alert=True)
            return
        await query.answer()
        if value == "refresh":
            await _send_menu(update, context)
            return
        reply_markup = None
        if value == "profile":
            profile = await _profile_for_update(update, service)
            blocks = build_profile_blocks(profile)
            fallback = (
                f"Досье: репутация {profile.reputation}, "
                f"агентов {profile.total_agents}."
            )
        elif value == "agents":
            await _profile_for_update(update, service)
            holdings = await service.get_agents(user.id)
            blocks = build_agents_blocks(holdings)
            fallback = "Агентурная сеть: " + (
                ", ".join(f"{item.agent_type} ×{item.amount}" for item in holdings)
                or "пока пуста"
            )
        elif value == "inventory":
            await _profile_for_update(update, service)
            inventory = await service.get_inventory(user.id)
            blocks = build_inventory_blocks(inventory)
            reply_markup = _inventory_keyboard(inventory)
            fallback = "Инвентарь: " + (
                ", ".join(
                    f"{item.item_type} ×{item.amount}" for item in inventory.items
                )
                or "пока пуст"
            )
        elif value == "contacts":
            recipes = service.settings.permanent_contact_recipes
            blocks = build_contact_blocks(recipes)
            reply_markup = _contact_keyboard(recipes)
            fallback = "Постоянные контакты Центра:\n" + "\n".join(
                recipe.display_name for recipe in recipes
            )
        elif value == "leaderboard":
            entries = await service.get_leaderboard()
            blocks = build_leaderboard_blocks(entries)
            fallback = "Рейтинг разведсетей:\n" + (
                "\n".join(
                    f"{entry.rank}. {entry.display_name}: {entry.total_agents}"
                    for entry in entries
                )
                or "пока пуст"
            )
        elif value == "status":
            status = await service.get_chat_status(chat.id)
            now = datetime.now(timezone.utc)
            blocks = build_status_blocks(status, now)
            fallback = (
                f"Активность: {status.activity_score:.1f}. "
                "Триггеры: пик, инерция, случайный сигнал."
            )
        await _send_rich(
            context,
            chat.id,
            blocks,
            fallback_text=fallback,
            reply_markup=reply_markup,
        )
        return

    if category.startswith("contact_"):
        recipe_id = category.removeprefix("contact_")
        try:
            result = await service.exchange_with_contact(
                operation_id=query.id,
                recipe_id=recipe_id,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception(
                "spy_game: permanent contact exchange failed recipe_id=%s",
                recipe_id,
            )
            await query.answer(
                "Центр не провёл сделку. Ресурсы не изменены.",
                show_alert=True,
            )
            return
        if result.status is NpcStatus.SUCCESS:
            await query.answer(
                f"Сделка завершена: {_format_drop_reward(result.reward)}",
                show_alert=True,
            )
        elif result.status is NpcStatus.INSUFFICIENT_RESOURCES:
            requirements = []
            if result.required_agents:
                requirements.append(_format_costs(result.required_agents))
            if result.required_items:
                requirements.append(_format_item_costs(result.required_items))
            await query.answer(
                "Для сделки нужно: " + "; ".join(requirements),
                show_alert=True,
            )
        else:
            await query.answer("Эта сделка недоступна.", show_alert=True)
        return

    if category == "agency":
        if value != "status":
            await query.answer("Некорректный запрос службы.", show_alert=True)
            return
        await query.answer()
        profile = await _profile_for_update(update, service)
        required_reputation = service.settings.agency_reputation_requirement(
            profile.agency_level
        )
        required_agents = service.settings.agency_requirements(profile.agency_level)
        at_cap = profile.agency_level >= service.settings.agency_max_level
        bonus = min(
            profile.agency_level * service.settings.agency_rare_bonus_percent,
            service.settings.agency_max_level
            * service.settings.agency_rare_bonus_percent,
        )
        blocks = [
            {"type": "paragraph", "text": "🏛 СОБСТВЕННАЯ РАЗВЕДСЛУЖБА"},
            {
                "type": "paragraph",
                "text": (
                    f"Текущий уровень: {profile.agency_level}/"
                    f"{service.settings.agency_max_level}. "
                    f"Постоянный бонус к редкому результату Рекрутера: +{bonus}%."
                ),
            },
            {
                "type": "paragraph",
                "text": (
                    "Следующий уровень требует репутацию "
                    f"{required_reputation} и: {_format_costs(required_agents)}."
                    if not at_cap
                    else "Достигнут максимальный уровень службы."
                ),
            },
            {
                "type": "footer",
                "text": (
                    "При создании уровня требуемые агенты будут списаны, "
                    "а репутация сброшена до 0. Остальные агенты и предметы сохранятся."
                ),
            },
        ]
        markup = None
        if not at_cap:
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Подтвердить создание службы",
                            callback_data=f"spy:agency_found:{profile.agency_level}",
                        )
                    ]
                ]
            )
        await _send_rich(
            context,
            chat.id,
            blocks,
            fallback_text=(
                f"Служба уровня {profile.agency_level}. Требуется репутация "
                f"{required_reputation} и {_format_costs(required_agents)}."
            ),
            reply_markup=markup,
        )
        return

    if category == "agency_found":
        try:
            expected_level = int(value)
            if expected_level < 0:
                raise ValueError
        except ValueError:
            await query.answer("Некорректный уровень службы.", show_alert=True)
            return
        try:
            result = await service.found_agency(
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
                expected_agency_level=expected_level,
            )
        except Exception:
            logger.exception("spy_game: agency founding failed user_id=%s", user.id)
            await query.answer(
                "Центр не зарегистрировал службу. Ресурсы не изменены.",
                show_alert=True,
            )
            return
        if result.status is AgencyStatus.SUCCESS:
            await query.answer(
                f"Разведслужба уровня {result.agency_level} создана.",
                show_alert=True,
            )
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: agency confirmation keyboard remained")
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "🏛 СЛУЖБА УЧРЕЖДЕНА"},
                    {
                        "type": "paragraph",
                        "text": (
                            f"{_public_label(user)} создал разведслужбу уровня "
                            f"{result.agency_level}."
                        ),
                    },
                ],
                fallback_text=(
                    f"🏛 {_public_label(user)} создал разведслужбу уровня "
                    f"{result.agency_level}."
                ),
            )
        elif result.status is AgencyStatus.INSUFFICIENT_RESOURCES:
            await query.answer(
                f"Нужно: репутация {result.required_reputation}; "
                f"{_format_costs(result.required_agents)}.",
                show_alert=True,
            )
        elif result.status is AgencyStatus.STALE:
            await query.answer(
                "Досье изменилось. Откройте условия службы заново.",
                show_alert=True,
            )
        elif result.status is AgencyStatus.MAX_LEVEL:
            await query.answer("Максимальный уровень уже достигнут.", show_alert=True)
        else:
            await query.answer("Создание службы сейчас недоступно.", show_alert=True)
        return

    if category == "equip":
        result = await service.equip_item(
            chat_id=chat.id,
            user_id=user.id,
            item_type=value,
        )
        if result.status is EquipmentStatus.SUCCESS:
            item = ITEM_TYPES[result.item_type]
            await query.answer(
                f"{item.display_name} установлен в слот {result.slot}.",
                show_alert=True,
            )
        elif result.status is EquipmentStatus.NO_FREE_SLOT:
            await query.answer("Все слоты заняты.", show_alert=True)
        elif result.status is EquipmentStatus.ALREADY_EQUIPPED:
            await query.answer("Этот предмет уже экипирован.", show_alert=True)
        else:
            await query.answer("Предмет нельзя экипировать.", show_alert=True)
        return

    if category == "unequip":
        try:
            slot = int(value)
        except ValueError:
            await query.answer("Некорректный слот.", show_alert=True)
            return
        result = await service.unequip_item(
            chat_id=chat.id,
            user_id=user.id,
            slot=slot,
        )
        if result.status is EquipmentStatus.SUCCESS:
            await query.answer("Предмет снят.", show_alert=True)
        else:
            await query.answer("Этот слот уже пуст.", show_alert=True)
        return

    if category == "search":
        try:
            result = await service.search_dead_drop(
                event_id=value,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: dead drop failed event_id=%s", value)
            await query.answer(
                "Не удалось открыть тайник. Попробуйте ещё раз.", show_alert=True
            )
            return
        if result.status is ClaimStatus.WON:
            reward = result.reward
            if reward.reward_type == "item":
                item = ITEM_TYPES[reward.reward_id]
                reward_text = f"{item.emoji} {item.display_name} ×{reward.amount}"
            elif reward.reward_type == "agent":
                agent = AGENT_TYPES[reward.reward_id]
                reward_text = f"{agent.emoji} {agent.display_name} ×{reward.amount}"
            else:
                reward_text = "тайник оказался пуст"
            await query.answer(f"Результат: {reward_text}", show_alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: dead drop could not remove keyboard")
            logger.info(
                "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
                "reward_type=%s reward_id=%s reward_amount=%s",
                result.event_id,
                chat.id,
                user.id,
                reward.reward_type,
                reward.reward_id,
                reward.amount,
            )
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "✅ ТАЙНИК ВСКРЫТ"},
                    {
                        "type": "paragraph",
                        "text": f"{_public_label(user)} нашёл: {reward_text}.",
                    },
                ],
                fallback_text=f"✅ {_public_label(user)} нашёл: {reward_text}.",
            )
        elif result.status is ClaimStatus.EXPIRED:
            await query.answer("Тайник уже изъят Центром.", show_alert=True)
        elif result.status is ClaimStatus.ALREADY_RESOLVED:
            await query.answer("Тайник уже обыскали.", show_alert=True)
        else:
            await query.answer("Тайник недоступен.", show_alert=True)
        return

    if category.startswith("intercept_"):
        choice_id = category.removeprefix("intercept_")
        try:
            result = await service.answer_intercept(
                event_id=value,
                choice_id=choice_id,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: intercept failed event_id=%s", value)
            await query.answer(
                "Не удалось проверить расшифровку. Попробуйте ещё раз.",
                show_alert=True,
            )
            return
        if result.status in {InterceptStatus.CORRECT, InterceptStatus.INCORRECT}:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: intercept could not remove keyboard")
            if result.status is InterceptStatus.CORRECT:
                item = ITEM_TYPES[result.reward.reward_id]
                answer = f"Верно: {item.display_name} зачислен."
                title = "✅ ШИФР РАСКРЫТ"
                body = (
                    f"{_public_label(user)} перехватил канал и получил "
                    f"{item.emoji} {item.display_name} ×{result.reward.amount}."
                )
            else:
                answer = "Неверная расшифровка. Канал сменил частоту."
                title = "❌ КАНАЛ ПОТЕРЯН"
                body = f"Ответ {_public_label(user)} оказался ложным следом."
            await query.answer(answer, show_alert=True)
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": title},
                    {"type": "paragraph", "text": body},
                ],
                fallback_text=f"{title}\n{body}",
            )
        elif result.status is InterceptStatus.EXPIRED:
            await query.answer("Канал уже замолчал.", show_alert=True)
        elif result.status is InterceptStatus.ALREADY_RESOLVED:
            await query.answer("Кто-то уже отправил ответ.", show_alert=True)
        else:
            await query.answer("Этот вариант ответа недоступен.", show_alert=True)
        return

    if category == "cooperate":
        try:
            result = await service.contribute_cooperative(
                event_id=value,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception(
                "spy_game: cooperative operation failed event_id=%s", value
            )
            await query.answer(
                "Центр не принял вклад. Попробуйте ещё раз.", show_alert=True
            )
            return
        if result.status is CooperativeStatus.CONTRIBUTED:
            await query.answer(
                f"Вклад принят: {result.contributions}/"
                f"{result.required_contributions} участников.",
                show_alert=True,
            )
        elif result.status is CooperativeStatus.COMPLETED:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: cooperative event could not remove keyboard")
            agent = AGENT_TYPES[result.reward.agent_type]
            await query.answer(
                f"Цель достигнута. Каждый получает {agent.display_name} "
                f"×{result.reward.amount}.",
                show_alert=True,
            )
            body = (
                f"{result.contributions} участников замкнули сеть наблюдения. "
                f"Каждому начислено: {agent.emoji} {agent.display_name} "
                f"×{result.reward.amount}."
            )
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "✅ ОПЕРАЦИЯ ЗАВЕРШЕНА"},
                    {"type": "paragraph", "text": body},
                ],
                fallback_text=f"✅ Операция завершена. {body}",
            )
        elif result.status is CooperativeStatus.ALREADY_CONTRIBUTED:
            await query.answer(
                f"Ваш вклад уже учтён: {result.contributions}/"
                f"{result.required_contributions}.",
                show_alert=True,
            )
        elif result.status is CooperativeStatus.ALREADY_RESOLVED:
            await query.answer("Операция уже укомплектована.", show_alert=True)
        elif result.status is CooperativeStatus.EXPIRED:
            await query.answer("Окно совместной операции закрыто.", show_alert=True)
        else:
            await query.answer("Операция недоступна.", show_alert=True)
        return

    if category == "chase":
        try:
            result = await service.advance_chase(
                event_id=value,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: chase failed event_id=%s", value)
            await query.answer("Погоня сорвалась. Попробуйте ещё раз.", show_alert=True)
            return
        if result.status is ChaseStatus.STARTED:
            await query.answer(
                "Преследование началось. Теперь цель нужно перехватить.",
                show_alert=True,
            )
            try:
                await query.edit_message_reply_markup(
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [
                                InlineKeyboardButton(
                                    "Перехватить цель",
                                    callback_data=f"spy:chase:{value}",
                                )
                            ]
                        ]
                    )
                )
            except Exception:
                logger.warning("spy_game: chase could not switch to stage two")
        elif result.status is ChaseStatus.COMPLETED:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: chase could not remove keyboard")
            await query.answer("Цель перехвачена. Награды начислены.", show_alert=True)
            starter_agent = AGENT_TYPES[result.starter_reward.agent_type]
            interceptor_agent = AGENT_TYPES[result.interceptor_reward.agent_type]
            body = (
                f"Первый этап: {result.starter_name} получает "
                f"{starter_agent.emoji} {starter_agent.display_name} "
                f"×{result.starter_reward.amount}.\n"
                f"Перехват: {result.interceptor_name} получает "
                f"{interceptor_agent.emoji} {interceptor_agent.display_name} "
                f"×{result.interceptor_reward.amount}."
            )
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "✅ ЦЕЛЬ ПЕРЕХВАЧЕНА"},
                    {"type": "paragraph", "text": body},
                ],
                fallback_text=f"✅ Цель перехвачена.\n{body}",
            )
        elif result.status is ChaseStatus.EXPIRED:
            await query.answer("Цель ушла от преследования.", show_alert=True)
        elif result.status is ChaseStatus.ALREADY_RESOLVED:
            await query.answer("Погоня уже завершена.", show_alert=True)
        else:
            await query.answer("Погоня недоступна.", show_alert=True)
        return

    if category.startswith("npc_"):
        recipe_id = category.removeprefix("npc_")
        try:
            result = await service.interact_with_npc(
                event_id=value,
                recipe_id=recipe_id,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: NPC interaction failed event_id=%s", value)
            await query.answer(
                "Специальный канал прервался. Ресурсы не изменены.",
                show_alert=True,
            )
            return
        if result.status is NpcStatus.SUCCESS:
            reward = result.reward
            if reward.reward_type == "agent":
                definition = AGENT_TYPES[reward.reward_id]
                reward_text = (
                    f"{definition.emoji} {definition.display_name} ×{reward.amount}"
                )
            else:
                definition = ITEM_TYPES[reward.reward_id]
                reward_text = (
                    f"{definition.emoji} {definition.display_name} ×{reward.amount}"
                )
            await query.answer(f"Сделка завершена: {reward_text}", show_alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: NPC event keyboard remained")
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "✅ СПЕЦИАЛЬНАЯ СДЕЛКА"},
                    {
                        "type": "paragraph",
                        "text": f"{_public_label(user)} получил: {reward_text}.",
                    },
                ],
                fallback_text=(
                    f"✅ {_public_label(user)} завершил сделку: {reward_text}."
                ),
            )
        elif result.status is NpcStatus.INSUFFICIENT_RESOURCES:
            requirements = []
            if result.required_agents:
                requirements.append(_format_costs(result.required_agents))
            if result.required_items:
                requirements.append(_format_item_costs(result.required_items))
            await query.answer(
                "Для сделки нужно: " + "; ".join(requirements),
                show_alert=True,
            )
        elif result.status is NpcStatus.EXPIRED:
            await query.answer("Специальный канал уже закрыт.", show_alert=True)
        elif result.status is NpcStatus.ALREADY_RESOLVED:
            await query.answer("Другой агент уже завершил сделку.", show_alert=True)
        else:
            await query.answer("Эта сделка недоступна.", show_alert=True)
        return

    if category == "death":
        try:
            result = await service.run_death_operation(
                event_id=value,
                action="death",
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: death operation failed event_id=%s", value)
            await query.answer(
                "Центр потерял связь. Состав не изменён, попробуйте ещё раз.",
                show_alert=True,
            )
            return
        if result.status is DeathOperationStatus.CONFIRMATION_REQUIRED:
            total = sum(holding.amount for holding in result.staked)
            seconds = max(
                1,
                math.ceil(
                    (
                        result.confirmation_expires_at - datetime.now(timezone.utc)
                    ).total_seconds()
                ),
            )
            await query.answer(
                f"На кону все ваши агенты: {total}. Шанс успеха "
                f"{service.settings.death_operation_success_percent}%. "
                f"Нажмите ещё раз в течение {seconds} сек., чтобы подтвердить.",
                show_alert=True,
            )
            return
        if result.status in {
            DeathOperationStatus.WON,
            DeathOperationStatus.LOST,
        }:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: death operation could not remove keyboard")
            total_staked = sum(holding.amount for holding in result.staked)
            if result.status is DeathOperationStatus.WON:
                bonus = AGENT_TYPES[result.rewards[-1].agent_type]
                title = "✅ НЕВОЗМОЖНОЕ ВЫПОЛНЕНО"
                body = (
                    f"{_public_label(user)} вернул сеть из {total_staked} агентов "
                    "в составе "
                    f"×{service.settings.death_operation_reward_multiplier} "
                    "и получил бонус: "
                    f"{bonus.emoji} {bonus.display_name}."
                )
                answer = "Операция успешна: состав удвоен, Tier 3 зачислен."
            else:
                title = "☠️ СВЯЗЬ ПОТЕРЯНА"
                body = (
                    f"{_public_label(user)} отправил на задание всю сеть — "
                    f"{total_staked} агентов. Никто не вернулся."
                )
                answer = "Операция провалена. Все поставленные агенты потеряны."
            await query.answer(answer, show_alert=True)
            logger.info(
                "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
                "outcome=%s stake=%s",
                result.event_id,
                chat.id,
                user.id,
                result.status.value,
                total_staked,
            )
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": title},
                    {"type": "paragraph", "text": body},
                ],
                fallback_text=f"{title}\n{body}",
            )
        elif result.status is DeathOperationStatus.INSUFFICIENT_AGENTS:
            await query.answer(
                "Для операции нужен хотя бы один агент.", show_alert=True
            )
        elif result.status is DeathOperationStatus.EXPIRED:
            await query.answer("Операция уже отменена Центром.", show_alert=True)
        elif result.status is DeathOperationStatus.ALREADY_RESOLVED:
            await query.answer("Другой игрок уже принял операцию.", show_alert=True)
        else:
            await query.answer("Операция недоступна.", show_alert=True)
        return

    if category == "prestige":
        try:
            expected_reputation = int(value)
            if expected_reputation < 0:
                raise ValueError
        except ValueError:
            await query.answer("Некорректный уровень репутации.", show_alert=True)
            return
        try:
            result = await service.increase_reputation(
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
                expected_reputation=expected_reputation,
            )
        except Exception:
            logger.exception("spy_game: prestige failed user_id=%s", user.id)
            await query.answer(
                "Центр не подтвердил повышение. Попробуйте ещё раз.",
                show_alert=True,
            )
            return
        if result.status is EconomyStatus.SUCCESS:
            await query.answer(
                f"Репутация повышена до {result.reputation}.", show_alert=True
            )
            logger.info(
                "spy_reputation_increased user_id=%s reputation=%s",
                user.id,
                result.reputation,
            )
            await _send_menu(update, context)
        elif result.status is EconomyStatus.INSUFFICIENT_RESOURCES:
            await query.answer(
                "Для повышения нужно: " + _format_costs(result.required),
                show_alert=True,
            )
        elif result.status is EconomyStatus.STALE:
            await query.answer(
                "Досье уже изменилось. Откройте свежее меню.", show_alert=True
            )
        else:
            await query.answer("Повышение сейчас недоступно.", show_alert=True)
        return

    if category.startswith("exchange_"):
        recipe_id = category.removeprefix("exchange_")
        try:
            result = await service.exchange_with_handler(
                event_id=value,
                recipe_id=recipe_id,
                chat_id=chat.id,
                user_id=user.id,
                username=user.username,
                display_name=_display_name(user),
            )
        except Exception:
            logger.exception("spy_game: exchange failed event_id=%s", value)
            await query.answer(
                "Куратор не подтвердил обмен. Попробуйте ещё раз.", show_alert=True
            )
            return
        if result.status is EconomyStatus.SUCCESS:
            agent = AGENT_TYPES[result.reward.agent_type]
            await query.answer(
                f"Обмен завершён: {agent.display_name} ×{result.reward.amount}",
                show_alert=True,
            )
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                logger.warning("spy_game: exchange could not remove event keyboard")
            logger.info(
                "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
                "reward_id=%s reward_amount=%s",
                result.event_id,
                chat.id,
                user.id,
                result.reward.agent_type,
                result.reward.amount,
            )
            await _send_rich(
                context,
                chat.id,
                [
                    {"type": "paragraph", "text": "✅ ОБМЕН ЗАВЕРШЁН"},
                    {
                        "type": "paragraph",
                        "text": (
                            f"{_public_label(user)} первым предъявил ресурсы.\n"
                            f"Новый агент: {agent.emoji} {agent.display_name} "
                            f"×{result.reward.amount}"
                        ),
                    },
                    {"type": "footer", "text": "Куратор закрыл дипломат."},
                ],
                fallback_text=(
                    f"✅ {_public_label(user)} завершает обмен: "
                    f"{agent.display_name} ×{result.reward.amount}."
                ),
            )
        elif result.status is EconomyStatus.INSUFFICIENT_RESOURCES:
            await query.answer(
                "Для обмена нужно: " + _format_costs(result.required),
                show_alert=True,
            )
        elif result.status is EconomyStatus.EXPIRED:
            await query.answer("Куратор уже ушёл.", show_alert=True)
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        elif result.status is EconomyStatus.ALREADY_RESOLVED:
            await query.answer("Другой агент уже завершил обмен.", show_alert=True)
        else:
            await query.answer("Этот обмен недоступен.", show_alert=True)
        return

    if category != "claim":
        await query.answer("Неизвестное действие.", show_alert=True)
        return
    try:
        result = await service.claim_event(
            event_id=value,
            action="claim",
            chat_id=chat.id,
            user_id=user.id,
            username=user.username,
            display_name=_display_name(user),
        )
    except Exception:
        logger.exception("spy_game: claim failed event_id=%s", value)
        await query.answer(
            "Связь с центром потеряна. Попробуйте ещё раз.", show_alert=True
        )
        return

    if result.status is ClaimStatus.WON:
        agent = AGENT_TYPES[result.reward.agent_type]
        await query.answer(
            f"Контакт {result.claims}/{result.required_claims}: "
            f"{agent.display_name} ×{result.reward.amount}",
            show_alert=True,
        )
        await _edit_recruitment_progress(context, query, service, value)
        logger.info(
            "spy_event_resolved event_id=%s chat_id=%s winner_id=%s "
            "reward_id=%s reward_amount=%s",
            result.event_id,
            chat.id,
            user.id,
            result.reward.agent_type,
            result.reward.amount,
        )
    elif result.status is ClaimStatus.ALREADY_CLAIMED:
        await query.answer(
            "Вы уже получили агента в этом наборе.",
            show_alert=True,
        )
    elif result.status is ClaimStatus.EXPIRED:
        await query.answer("Окно контакта уже закрылось.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
    elif result.status is ClaimStatus.ALREADY_RESOLVED:
        await query.answer("Другой агент оказался быстрее.", show_alert=True)
    elif result.status is ClaimStatus.DISABLED:
        await query.answer("Разведсеть сейчас отключена.", show_alert=True)
    else:
        await query.answer("Этот сигнал больше недействителен.", show_alert=True)


async def track_spy_activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if (
        user is None
        or user.is_bot
        or chat is None
        or chat.type not in {"group", "supergroup"}
        or context.bot_data.get("paused", False)
    ):
        return
    await _service(context).record_activity(chat.id, user.id)


async def spy_game_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    service = _service(context)
    try:
        result = await service.tick()
    except Exception:
        logger.exception("spy_game: scheduler tick failed")
        return
    for expired in result.expired:
        await _remove_event_keyboard(
            context,
            expired.chat_id,
            expired.message_id,
        )
    for event in result.spawned:
        try:
            message_id = await publish_spy_event(context, event)
            await service.attach_message(event.event_id, message_id)
            logger.info(
                "spy_event_created event_id=%s chat_id=%s event_type=%s expires_at=%s",
                event.event_id,
                event.chat_id,
                event.event_type,
                event.expires_at.isoformat(),
            )
        except Exception:
            logger.exception(
                "spy_game: event publication failed event_id=%s", event.event_id
            )
            await service.cancel_publication(event.event_id)


def build_activity_admin_text(
    status: ChatStatus,
    trigger: ActivityTriggerSettings,
) -> str:
    label = ACTIVITY_PROFILE_LABELS[trigger.profile]
    return (
        "⚙️ ЧАСТОТА СОБЫТИЙ\n"
        f"Профиль: {label} ({trigger.profile})\n"
        f"Текущий score: {status.activity_score:.1f}\n"
        f"Peak: score ≥ {trigger.threshold:g} и сообщений за tick ≥ "
        f"{trigger.peak_messages}\n"
        f"Inertia: 1/{trigger.inertia_one_in} за tick в течение "
        f"{math.ceil(trigger.inertia_window_seconds / 60)} мин.\n"
        f"Случайный сигнал: в среднем раз в "
        f"{math.ceil(trigger.random_average_seconds / 60)} мин.\n"
        f"Cooldown: {math.ceil(trigger.event_cooldown_seconds / 60)} мин.\n\n"
        "Переключение: /spy_admin activity "
        "calm|balanced|aggressive"
    )


async def spy_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.effective_chat is None:
        return
    if update.effective_user.id != context.bot_data.get("master"):
        await update.effective_message.reply_text("Команда доступна только master.")
        return
    service = _service(context)
    action = context.args[0].lower() if context.args else "status"
    chat_id = update.effective_chat.id
    if action in {"enable", "spawn", "activity"} and update.effective_chat.type not in {
        "group",
        "supergroup",
    }:
        await update.effective_message.reply_text(
            "Эта операция доступна только в group/supergroup."
        )
        return
    if action == "enable":
        result = await service.enable_chat(chat_id)
    elif action == "disable":
        result = await service.disable_chat(chat_id)
        await _remove_event_keyboard(context, chat_id, result.message_id_to_close)
    elif action == "spawn":
        event_type = context.args[1].lower() if len(context.args) > 1 else "recruitment"
        result = await service.manual_spawn(chat_id, event_type=event_type)
        if result.event is not None:
            try:
                message_id = await publish_spy_event(context, result.event)
                await service.attach_message(result.event.event_id, message_id)
                logger.info(
                    "spy_event_created event_id=%s chat_id=%s event_type=%s "
                    "expires_at=%s",
                    result.event.event_id,
                    result.event.chat_id,
                    result.event.event_type,
                    result.event.expires_at.isoformat(),
                )
            except Exception:
                logger.exception(
                    "spy_game: manual publication failed event_id=%s",
                    result.event.event_id,
                )
                await service.cancel_publication(result.event.event_id)
                result = type(result)(False, "Не удалось опубликовать событие.")
    elif action == "activity":
        if len(context.args) > 1:
            requested = ACTIVITY_PROFILE_ALIASES.get(context.args[1].lower())
            if requested is None:
                await update.effective_message.reply_text(
                    "Неизвестный профиль. Используйте calm, balanced или aggressive."
                )
                return
            result = await service.set_activity_profile(chat_id, requested)
            if not result.ok:
                await update.effective_message.reply_text(result.message)
                return
            logger.info(
                "spy_admin_action action=activity profile=%s chat_id=%s "
                "requested_by=%s",
                requested,
                chat_id,
                update.effective_user.id,
            )
        status = await service.get_chat_status(chat_id)
        trigger = service.activity_trigger_settings(status.activity_profile)
        await update.effective_message.reply_text(
            build_activity_admin_text(status, trigger)
        )
        return
    elif action == "status":
        status = await service.get_chat_status(chat_id)
        now = datetime.now(timezone.utc)
        blocks = build_status_blocks(status, now)
        blocks.insert(
            -1,
            {
                "type": "details",
                "summary": "Runtime",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": (
                            "Narrator: "
                            + (
                                "LLM + template fallback"
                                if service.settings.llm_narrator_enabled
                                else "template"
                            )
                            + f"\nActivity profile: {status.activity_profile}"
                        ),
                    }
                ],
            },
        )
        await _send_rich(
            context,
            chat_id,
            blocks,
            fallback_text=(
                f"enabled={status.enabled}, activity={status.activity_score:.1f}, "
                f"profile={status.activity_profile}, next={status.next_event_at}, "
                f"active={status.active_event_id}"
            ),
        )
        return
    else:
        await update.effective_message.reply_text(
            "Использование: /spy_admin enable|disable|spawn "
            "[recruitment|dead_drop|intercept|cooperative_operation|chase|"
            "handler|npc|death_operation]|activity "
            "[calm|balanced|aggressive]|status"
        )
        return
    if result.ok:
        logger.info(
            "spy_admin_action action=%s chat_id=%s requested_by=%s",
            action,
            chat_id,
            update.effective_user.id,
        )
    await update.effective_message.reply_text(result.message)
