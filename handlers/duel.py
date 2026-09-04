"""AI game master powered one-round duels between two chat members."""

import json
import random
import re
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.ext import ContextTypes

import const
from config import client, logger
from guards import ensure_master_in_chat_for_ai, pause
from spy_game.models import DuelWager, DuelWagerStatus
from spy_game.service import SpyGameService
from spy_game.settings import AGENT_TYPES
from state import ensure_chat_state, remember_chat_user
from telegram_utils import parse_stream

DUEL_CALLBACK_PATTERN = r"^duel:"
DUEL_MODEL = "deepseek-v4-flash"
DUEL_ACCEPT_TIMEOUT_SECONDS = 120
DUEL_MOVE_TIMEOUT_SECONDS = 180

_ACTIONS = {
    "attack": ("Атаковать", "переходит в прямую атаку"),
    "defend": ("Защищаться", "занимает оборонительную позицию"),
    "trick": ("Обмануть", "пытается обмануть соперника"),
    "environment": ("Использовать окружение", "использует окружение"),
    "risk": ("Рискованный манёвр", "совершает рискованный манёвр"),
}

_FALLBACK_SCENARIOS = (
    {
        "title": "Операция «Последний вагон»",
        "setting": "Ночной поезд мчится к границе. Свет погас, а на полу скользят незакреплённые чемоданы.",
        "condition": "Побеждает тот, кто первым обезоружит соперника или вытеснит его из вагона-ресторана.",
    },
    {
        "title": "Операция «Разбитый багет»",
        "setting": "Крыша марсельского отеля под ливнем. Рядом один вертолёт, но двигатель ещё не запущен.",
        "condition": "Побеждает тот, кто получит контроль над вертолётом и не даст сопернику подняться на борт.",
    },
    {
        "title": "Операция «Тихий аукцион»",
        "setting": "Закрытый аукционный зал после полуночи. Между дуэлянтами витрины, сигнализация и один подозрительный чемодан.",
        "condition": "Побеждает тот, кто завладеет чемоданом и удержит его до прибытия эвакуационной группы.",
    },
)

_BEATS = {
    "attack": "environment",
    "environment": "risk",
    "risk": "trick",
    "trick": "defend",
    "defend": "attack",
}


def _compact(value: str, max_length: int) -> str:
    return " ".join(value.strip().split())[:max_length]


def _duel_ai_messages(prompt: str) -> list[dict]:
    """Build every duel AI request with the mandatory Belmondo persona."""
    return [
        {"role": "system", "content": const.professional_prompt},
        {"role": "user", "content": prompt},
    ]


def _naturalize_judgement_text(text: str, duel: dict) -> str:
    """Replace internal player role labels with names safe for user-facing text."""
    replacements = {
        "challenger": duel["players"]["challenger"]["name"],
        "opponent": duel["players"]["opponent"]["name"],
    }
    result = text
    for role, name in replacements.items():
        result = re.sub(rf"\b{role}\b", lambda _: name, result, flags=re.IGNORECASE)
    return result


def _duel_store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return ensure_chat_state(context)["duels"]


def _spy_service(context: ContextTypes.DEFAULT_TYPE) -> SpyGameService | None:
    service = getattr(context, "bot_data", {}).get("spy_game")
    return service if isinstance(service, SpyGameService) else None


def _spy_public_name(user) -> str:
    return f"@{user.username.lstrip('@')}" if user.username else "Скрытый агент"


def _agent_amount_text(agent_type: str, amount: int) -> str:
    agent = AGENT_TYPES[agent_type]
    return f"{agent.emoji} {agent.display_name} ×{amount}"


def _requested_stake(context: ContextTypes.DEFAULT_TYPE) -> int:
    args = tuple(getattr(context, "args", ()) or ())
    return int(args[-1]) if args and args[-1].isdigit() else 1


def _duel_data_from_wager(wager: DuelWager) -> dict:
    actions = {}
    if wager.challenger_action and wager.challenger_user_id is not None:
        actions[str(wager.challenger_user_id)] = wager.challenger_action
    if wager.opponent_action and wager.opponent_user_id is not None:
        actions[str(wager.opponent_user_id)] = wager.opponent_action
    return {
        "id": wager.duel_id,
        "status": wager.status.value,
        "wagered": True,
        "stake_amount": wager.stake_amount,
        "agent_type": wager.agent_type,
        "players": {
            "challenger": {
                "id": wager.challenger_user_id,
                "name": wager.challenger_name,
            },
            "opponent": {
                "id": wager.opponent_user_id,
                "name": wager.opponent_name,
                "username": wager.opponent_username,
            },
        },
        "scenario": wager.scenario,
        "actions": actions,
    }


def _display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name


def _extract_json(text: str) -> dict:
    """Extract a JSON object from a model response."""
    source = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", source, flags=re.S)
    if fenced:
        source = fenced.group(1)
    else:
        start = source.find("{")
        end = source.rfind("}")
        if start >= 0 and end > start:
            end += 1
            source = source[start:end]
    return json.loads(source)


async def _generate_scenario(challenger_name: str, opponent_name: str) -> dict:
    prompt = (
        "Ты гейм-мастер короткой шуточной дуэли двух профессионалов. "
        f"Дуэлянты: {challenger_name} и {opponent_name}. "
        "Придумай кинематографичную, но компактную сцену без преимущества для одного из игроков. "
        "Верни только JSON с ключами title, setting, condition. "
        "Каждое значение должно быть строкой, setting и condition — не длиннее двух предложений."
    )
    try:
        stream = await client.chat.completions.create(
            model=DUEL_MODEL,
            messages=_duel_ai_messages(prompt),
            stream=True,
        )
        result = _extract_json(await parse_stream(stream))
        if all(
            isinstance(result.get(key), str)
            for key in ("title", "setting", "condition")
        ):
            return {
                "title": _compact(result["title"], 120),
                "setting": _compact(result["setting"], 700),
                "condition": _compact(result["condition"], 400),
            }
    except Exception:
        logger.exception("duel: failed to generate scenario")
    return dict(random.choice(_FALLBACK_SCENARIOS))


async def _judge_duel(duel: dict) -> dict:
    challenger = duel["players"]["challenger"]
    opponent = duel["players"]["opponent"]
    challenger_action = _ACTIONS[duel["actions"][str(challenger["id"])]][1]
    opponent_action = _ACTIONS[duel["actions"][str(opponent["id"])]][1]
    scenario = duel["scenario"]
    prompt = (
        "Ты беспристрастный гейм-мастер дуэли. Выбери ровно одного победителя, "
        "исходя из сцены, условия победы и выбранных действий. Не меняй правила и не объявляй ничью.\n\n"
        f"Название: {scenario['title']}\n"
        f"Сцена: {scenario['setting']}\n"
        f"Условие победы: {scenario['condition']}\n"
        f"Первый дуэлянт, {challenger['name']}: {challenger_action}.\n"
        f"Второй дуэлянт, {opponent['name']}: {opponent_action}.\n\n"
        "В полях narration и reason называй участников только по указанным именам. "
        "Никогда не используй в художественном тексте служебные слова challenger и opponent. "
        "Они допустимы только как значение поля winner.\n\n"
        "Верни только JSON: "
        '{"winner":"challenger или opponent","narration":"2-4 предложения о ходе и финале дуэли",'
        '"reason":"одно короткое предложение, почему победил именно этот игрок"}'
    )
    try:
        stream = await client.chat.completions.create(
            model=DUEL_MODEL,
            messages=_duel_ai_messages(prompt),
            stream=True,
        )
        result = _extract_json(await parse_stream(stream))
        if (
            result.get("winner") in ("challenger", "opponent")
            and isinstance(result.get("narration"), str)
            and isinstance(result.get("reason"), str)
        ):
            return {
                "winner": result["winner"],
                "narration": _compact(
                    _naturalize_judgement_text(result["narration"], duel), 1800
                ),
                "reason": _compact(
                    _naturalize_judgement_text(result["reason"], duel), 500
                ),
            }
    except Exception:
        logger.exception("duel: failed to judge duel")
    return _fallback_judgement(duel)


async def _narrate_wagered_duel(duel: dict, winner_key: str) -> dict:
    """Narrate a server-settled wager without letting AI change its winner."""
    challenger = duel["players"]["challenger"]
    opponent = duel["players"]["opponent"]
    scenario = duel["scenario"]
    winner = duel["players"][winner_key]
    prompt = (
        "Ты комментатор уже завершённой дуэли. Победитель определён сервером, "
        "его нельзя менять. Напиши кинематографичный финал без ничьей.\n\n"
        f"Название: {scenario['title']}\n"
        f"Сцена: {scenario['setting']}\n"
        f"Условие победы: {scenario['condition']}\n"
        f"{challenger['name']}: "
        f"{_ACTIONS[duel['actions'][str(challenger['id'])]][1]}.\n"
        f"{opponent['name']}: "
        f"{_ACTIONS[duel['actions'][str(opponent['id'])]][1]}.\n"
        f"Зафиксированный победитель: {winner['name']}.\n\n"
        "Верни только JSON с полями narration и reason. В narration — 2-4 "
        "предложения, в reason — одно короткое предложение."
    )
    try:
        stream = await client.chat.completions.create(
            model=DUEL_MODEL,
            messages=_duel_ai_messages(prompt),
            stream=True,
        )
        result = _extract_json(await parse_stream(stream))
        if isinstance(result.get("narration"), str) and isinstance(
            result.get("reason"), str
        ):
            return {
                "winner": winner_key,
                "narration": _compact(
                    _naturalize_judgement_text(result["narration"], duel), 1800
                ),
                "reason": _compact(
                    _naturalize_judgement_text(result["reason"], duel), 500
                ),
            }
    except Exception:
        logger.exception("duel: failed to narrate wagered duel")
    return {
        "winner": winner_key,
        "narration": (
            "Связь с комментатором прервалась, но полевая автоматика уже "
            f"зафиксировала результат: инициативу удержал {winner['name']}."
        ),
        "reason": "Комбинация ходов разрешена по протоколу дуэльной подготовки.",
    }


def _fallback_judgement(duel: dict) -> dict:
    """Resolve a duel deterministically enough when the game master is unavailable."""
    challenger = duel["players"]["challenger"]
    opponent = duel["players"]["opponent"]
    challenger_action = duel["actions"][str(challenger["id"])]
    opponent_action = duel["actions"][str(opponent["id"])]

    if _BEATS[challenger_action] == opponent_action:
        winner = "challenger"
    elif _BEATS[opponent_action] == challenger_action:
        winner = "opponent"
    else:
        winner = random.choice(("challenger", "opponent"))

    winner_name = duel["players"][winner]["name"]
    return {
        "winner": winner,
        "narration": (
            "Связь с гейм-мастером прервалась, и исход решила полевая инструкция. "
            f"После короткой, но убедительной схватки инициативу захватывает {winner_name}."
        ),
        "reason": "Выбранный ход оказался эффективнее в прямом столкновении.",
    }


def _challenge_keyboard(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Принять вызов", callback_data=f"duel:{duel_id}:accept"
                ),
                InlineKeyboardButton(
                    "Отступить", callback_data=f"duel:{duel_id}:decline"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Отменить вызов", callback_data=f"duel:{duel_id}:cancel"
                )
            ],
        ]
    )


def _actions_keyboard(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⚔️ Атаковать", callback_data=f"duel:{duel_id}:move:attack"
                ),
                InlineKeyboardButton(
                    "🛡 Защищаться", callback_data=f"duel:{duel_id}:move:defend"
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎭 Обмануть", callback_data=f"duel:{duel_id}:move:trick"
                ),
                InlineKeyboardButton(
                    "🔎 Окружение", callback_data=f"duel:{duel_id}:move:environment"
                ),
            ],
            [
                InlineKeyboardButton(
                    "💥 Рискованный манёвр", callback_data=f"duel:{duel_id}:move:risk"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏳 Сдаться", callback_data=f"duel:{duel_id}:forfeit"
                )
            ],
        ]
    )


def _challenge_text(duel: dict) -> str:
    challenger = duel["players"]["challenger"]["name"]
    opponent = duel["players"]["opponent"]["name"]
    scenario = duel["scenario"]
    wager = ""
    if duel.get("wagered"):
        amount = duel["stake_amount"]
        wager = (
            f"\n\nСтавка каждого: "
            f"{_agent_amount_text(duel['agent_type'], amount)}. "
            f"Банк победителя: ×{amount * 2}."
        )
    return (
        f"{scenario['title']}\n\n"
        f"{scenario['setting']}\n\n"
        f"Условие победы: {scenario['condition']}\n\n"
        f"{challenger} вызывает {opponent} на дуэль профессионалов.\n"
        "Только вызванный дуэлянт может принять или отклонить вызов.\n"
        f"На принятие вызова даётся {DUEL_ACCEPT_TIMEOUT_SECONDS} секунд."
        f"{wager}"
    )


def _round_text(duel: dict) -> str:
    challenger = duel["players"]["challenger"]
    opponent = duel["players"]["opponent"]
    selected = duel["actions"]

    def status(player: dict) -> str:
        return "ход выбран" if str(player["id"]) in selected else "ожидается ход"

    wager = ""
    if duel.get("wagered"):
        wager = (
            f"\nБанк: "
            f"{_agent_amount_text(duel['agent_type'], duel['stake_amount'] * 2)}."
        )
    return (
        f"{duel['scenario']['title']}\n\n"
        f"{duel['scenario']['setting']}\n\n"
        f"Условие победы: {duel['scenario']['condition']}\n\n"
        "Гейм-мастер наблюдает. Выберите действия тайно:\n"
        f"• {challenger['name']}: {status(challenger)}\n"
        f"• {opponent['name']}: {status(opponent)}"
        f"{wager}"
    )


def _resolve_opponent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    for entity in message.entities or ():
        if entity.type == MessageEntity.TEXT_MENTION and entity.user:
            return entity.user
        if entity.type == MessageEntity.MENTION:
            username = message.parse_entity(entity).lstrip("@").lower()
            return (
                ensure_chat_state(context)["known_chat_users"].get(username) or username
            )
    return None


def _cancel_accept_timeout(context: ContextTypes.DEFAULT_TYPE, duel_data: dict) -> None:
    job_queue = getattr(context, "job_queue", None)
    timeout_job_name = duel_data.get("timeout_job_name")
    if job_queue is None or timeout_job_name is None:
        return
    for job in job_queue.get_jobs_by_name(timeout_job_name):
        job.schedule_removal()


async def duel_accept_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Expire a pending challenge and remove its inactive buttons."""
    data = context.job.data
    duel_id = data["duel_id"]
    duels = _duel_store(context)
    duel_data = duels.get(duel_id)
    if duel_data is None or duel_data["status"] != "pending":
        return

    if duel_data.get("wagered"):
        service = _spy_service(context)
        if service is None:
            logger.error("duel: wager service unavailable on timeout duel=%s", duel_id)
            return
        result = await service.expire_duel_wager(duel_id)
        if (
            result.status is not DuelWagerStatus.REFUNDED
            or result.resolution != "accept_timeout"
        ):
            return
    duels.pop(duel_id, None)
    text = (
        f"{duel_data['players']['opponent']['name']} не принял вызов за "
        f"{DUEL_ACCEPT_TIMEOUT_SECONDS} секунд.\n\n"
        "Гейм-мастер закрывает дело. Время вышло."
        + (" Ставка возвращена." if duel_data.get("wagered") else "")
    )
    try:
        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=text,
        )
    except Exception:
        logger.exception("duel: failed to edit expired challenge duel=%s", duel_id)
    logger.info("duel: challenge expired chat=%s duel=%s", data["chat_id"], duel_id)


async def duel_move_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Settle or refund a wagered duel when the move window closes."""
    data = context.job.data
    duel_id = data["duel_id"]
    duel_data = _duel_store(context).get(duel_id)
    service = _spy_service(context)
    if duel_data is None or service is None or not duel_data.get("wagered"):
        return
    result = await service.expire_duel_wager(duel_id)
    if result.resolution not in {"move_timeout", "move_timeout_no_moves"}:
        return
    _duel_store(context).pop(duel_id, None)
    if result.status is DuelWagerStatus.WON:
        text = (
            "Время на ход вышло. Техническая победа присуждена агенту, "
            f"который успел сделать ход.\n\nПобедитель: {result.winner_name}\n"
            f"Банк: "
            f"{_agent_amount_text(result.agent_type, result.stake_amount * 2)}."
        )
    else:
        text = "Ни один дуэлянт не сделал ход вовремя. Обе ставки возвращены."
    try:
        await context.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=text,
        )
    except Exception:
        logger.exception("duel: failed to edit move timeout duel=%s", duel_id)


@pause
async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a duel challenge against a mentioned or replied-to user."""
    if (
        update.message is None
        or update.effective_user is None
        or update.effective_chat is None
    ):
        return
    if not await ensure_master_in_chat_for_ai(update, context):
        return

    remember_chat_user(context, update.effective_user)
    opponent = _resolve_opponent(update, context)
    if opponent is None:
        await update.message.reply_text(
            "Не удалось определить соперника. Ответь командой /duel на его сообщение "
            "или добавь @username после команды."
        )
        return

    duels = _duel_store(context)
    if duels:
        await update.message.reply_text(
            "В этом чате уже идёт дуэль. Дождитесь её завершения."
        )
        return

    stake_amount = _requested_stake(context)
    service = _spy_service(context)
    if service is not None:
        active_wager = await service.get_active_duel_wager(update.effective_chat.id)
        if active_wager.status in {
            DuelWagerStatus.PENDING,
            DuelWagerStatus.CHOOSING,
        }:
            await update.message.reply_text(
                "В этом чате уже идёт ставочная дуэль. Дождитесь её завершения."
            )
            return
    if stake_amount is not None:
        if service is None or not service.chat_is_available(update.effective_chat.id):
            await update.message.reply_text(
                "Ставочная дуэль доступна только в активированном Spy Clicker чате."
            )
            return
        if stake_amount not in service.settings.duel_stake_amounts:
            allowed = ", ".join(map(str, service.settings.duel_stake_amounts))
            await update.message.reply_text(
                f"Допустимые ставки осведомителями: {allowed}."
            )
            return

    duel_id = secrets.token_hex(4)
    challenger_data = {
        "id": update.effective_user.id,
        "name": (
            _spy_public_name(update.effective_user)
            if stake_amount is not None
            else _display_name(update.effective_user)
        ),
    }
    if isinstance(opponent, str):
        if (
            update.effective_user.username
            and opponent == update.effective_user.username.lower()
        ):
            await update.message.reply_text(
                "Дуэль с самим собой отменена медицинской комиссией."
            )
            return
        opponent_data = {"id": None, "name": f"@{opponent}", "username": opponent}
    else:
        if opponent.id == update.effective_user.id:
            await update.message.reply_text(
                "Дуэль с самим собой отменена медицинской комиссией."
            )
            return
        if opponent.is_bot:
            await update.message.reply_text(
                "Другие боты не допущены к профессиональным дуэлям."
            )
            return
        opponent_data = {
            "id": opponent.id,
            "name": _spy_public_name(opponent)
            if stake_amount is not None
            else _display_name(opponent),
            "username": opponent.username.lower() if opponent.username else None,
        }
    scenario = await _generate_scenario(challenger_data["name"], opponent_data["name"])
    timeout_job_name = f"duel-accept-timeout:{update.effective_chat.id}:{duel_id}"
    if stake_amount is not None:
        wager = await service.create_duel_wager(
            duel_id=duel_id,
            chat_id=update.effective_chat.id,
            challenger_user_id=update.effective_user.id,
            challenger_username=update.effective_user.username,
            challenger_display_name=update.effective_user.full_name,
            opponent_user_id=opponent_data["id"],
            opponent_username=opponent_data.get("username"),
            opponent_display_name=(
                opponent.full_name if not isinstance(opponent, str) else None
            ),
            stake_amount=stake_amount,
            scenario=scenario,
        )
        if wager.status is DuelWagerStatus.INSUFFICIENT_AGENTS:
            await update.message.reply_text(
                f"Для вызова нужно минимум {stake_amount} осведомителей."
            )
            return
        if wager.status is DuelWagerStatus.ACTIVE_DUEL_EXISTS:
            await update.message.reply_text(
                "В этом чате уже идёт ставочная дуэль. Дождитесь её завершения."
            )
            return
        if wager.status is DuelWagerStatus.DISABLED:
            await update.message.reply_text(
                "Сначала владелец должен включить Spy Clicker в этом чате."
            )
            return
        if wager.status is not DuelWagerStatus.PENDING:
            await update.message.reply_text("Не удалось зарезервировать ставку.")
            return
        duel_data = _duel_data_from_wager(wager)
        duel_data["timeout_job_name"] = timeout_job_name
    else:
        duel_data = {
            "id": duel_id,
            "status": "pending",
            "timeout_job_name": timeout_job_name,
            "players": {"challenger": challenger_data, "opponent": opponent_data},
            "scenario": scenario,
            "actions": {},
        }
    duels[duel_id] = duel_data
    try:
        challenge_message = await update.message.reply_text(
            _challenge_text(duel_data), reply_markup=_challenge_keyboard(duel_id)
        )
    except Exception:
        duels.pop(duel_id, None)
        if stake_amount is not None:
            await service.cancel_duel_wager_as_master(duel_id)
        raise
    if stake_amount is not None:
        await service.attach_duel_message(duel_id, challenge_message.message_id)
    if context.job_queue is None:
        logger.error(
            "duel: JobQueue is unavailable; acceptance timeout was not scheduled"
        )
    else:
        context.job_queue.run_once(
            duel_accept_timeout,
            (
                service.settings.duel_accept_seconds
                if stake_amount is not None
                else DUEL_ACCEPT_TIMEOUT_SECONDS
            ),
            data={
                "duel_id": duel_id,
                "chat_id": update.effective_chat.id,
                "message_id": challenge_message.message_id,
            },
            name=duel_data["timeout_job_name"],
            chat_id=update.effective_chat.id,
        )
    logger.info(
        "duel: challenge created chat=%s challenger=%s opponent=%s",
        update.effective_chat.id,
        challenger_data["id"],
        opponent_data["id"],
    )


async def duel_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the active duel when requested by a participant or the bot master."""
    if (
        update.message is None
        or update.effective_user is None
        or update.effective_chat is None
    ):
        return

    duels = _duel_store(context)
    if not duels:
        service = _spy_service(context)
        wager = (
            await service.get_active_duel_wager(update.effective_chat.id)
            if service is not None
            else None
        )
        if wager is None or wager.status not in {
            DuelWagerStatus.PENDING,
            DuelWagerStatus.CHOOSING,
        }:
            await update.message.reply_text("В этом чате нет активной дуэли.")
            return
        duel_data = _duel_data_from_wager(wager)
        duels[wager.duel_id] = duel_data

    duel_id, duel_data = next(iter(duels.items()))
    participant_ids = {
        duel_data["players"]["challenger"]["id"],
        duel_data["players"]["opponent"]["id"],
    }
    master_id = context.bot_data["master"]
    username = getattr(update.effective_user, "username", None)
    username = username.lower() if username else None
    is_named_opponent = duel_data["players"]["opponent"][
        "id"
    ] is None and username == duel_data["players"]["opponent"].get("username")
    allowed_ids = participant_ids | {master_id}
    if update.effective_user.id not in allowed_ids and not is_named_opponent:
        await update.message.reply_text("Отменить эту дуэль могут только её участники.")
        return

    _cancel_accept_timeout(context, duel_data)
    if duel_data.get("wagered"):
        service = _spy_service(context)
        if service is None:
            await update.message.reply_text("Экономика Spy Clicker недоступна.")
            return
        is_master = update.effective_user.id == master_id
        if is_master:
            result = await service.cancel_duel_wager_as_master(duel_id)
            outcome = "Дуэль отменена владельцем. Обе ставки возвращены."
        elif duel_data["status"] == "pending":
            close_action = (
                "cancel"
                if update.effective_user.id == duel_data["players"]["challenger"]["id"]
                else "decline"
            )
            result = await service.close_pending_duel_wager(
                duel_id=duel_id,
                user_id=update.effective_user.id,
                username=update.effective_user.username,
                action=close_action,
            )
            outcome = "Вызов отменён. Ставка возвращена."
        else:
            result = await service.forfeit_duel_wager(
                duel_id,
                update.effective_user.id,
            )
            outcome = (
                f"Дуэлянт сдаётся. Победитель: {result.winner_name}. "
                f"Банк: "
                f"{_agent_amount_text(result.agent_type, result.stake_amount * 2)}."
            )
        if result.status not in {DuelWagerStatus.WON, DuelWagerStatus.REFUNDED}:
            await update.message.reply_text("Не удалось закрыть ставочную дуэль.")
            return
        duels.pop(duel_id, None)
        await update.message.reply_text(outcome)
        return
    duels.pop(duel_id, None)
    await update.message.reply_text("Дуэль отменена. Гейм-мастер закрывает дело.")
    logger.info(
        "duel: cancelled chat=%s duel=%s by=%s",
        update.effective_chat.id,
        duel_id,
        update.effective_user.id,
    )


async def duel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle challenge and move buttons while enforcing participant ownership."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        await query.answer("Некорректная команда дуэли.", show_alert=True)
        return

    duel_id, action = parts[1], parts[2]
    duels = _duel_store(context)
    duel_data = duels.get(duel_id)
    if duel_data is None:
        service = _spy_service(context)
        wager = await service.get_duel_wager(duel_id) if service is not None else None
        if wager is None or wager.status is DuelWagerStatus.NOT_FOUND:
            await query.answer(
                "Эта дуэль уже завершена или потеряна после перезапуска.",
                show_alert=True,
            )
            return
        if wager.status in {DuelWagerStatus.WON, DuelWagerStatus.REFUNDED}:
            await query.answer("Эта ставочная дуэль уже рассчитана.", show_alert=True)
            return
        duel_data = _duel_data_from_wager(wager)
        duels[duel_id] = duel_data

    challenger_id = duel_data["players"]["challenger"]["id"]
    opponent_id = duel_data["players"]["opponent"]["id"]
    user_id = update.effective_user.id
    opponent_username = duel_data["players"]["opponent"].get("username")
    callback_username_value = getattr(update.effective_user, "username", None)
    callback_username = (
        callback_username_value.lower() if callback_username_value else None
    )
    is_opponent = user_id == opponent_id or (
        opponent_id is None and callback_username == opponent_username
    )

    if action in ("accept", "decline") and not is_opponent:
        await query.answer(
            "Эта кнопка предназначена только вызванному дуэлянту.", show_alert=True
        )
        return
    if action == "cancel" and user_id != challenger_id:
        await query.answer("Отменить вызов может только его автор.", show_alert=True)
        return

    if action == "decline":
        if duel_data.get("wagered"):
            result = await _spy_service(context).close_pending_duel_wager(
                duel_id=duel_id,
                user_id=user_id,
                username=callback_username_value,
                action="decline",
            )
            if result.status is not DuelWagerStatus.REFUNDED:
                await query.answer("Не удалось вернуть ставку.", show_alert=True)
                return
        await query.answer()
        _cancel_accept_timeout(context, duel_data)
        duels.pop(duel_id, None)
        await query.edit_message_text(
            f"{duel_data['players']['opponent']['name']} отказывается от дуэли. "
            "C'est la vie."
            + (" Ставка возвращена." if duel_data.get("wagered") else "")
        )
        return

    if action == "cancel":
        if duel_data.get("wagered"):
            result = await _spy_service(context).close_pending_duel_wager(
                duel_id=duel_id,
                user_id=user_id,
                username=callback_username_value,
                action="cancel",
            )
            if result.status is not DuelWagerStatus.REFUNDED:
                await query.answer("Не удалось вернуть ставку.", show_alert=True)
                return
        await query.answer()
        _cancel_accept_timeout(context, duel_data)
        duels.pop(duel_id, None)
        await query.edit_message_text(
            "Вызов отозван. Операция отменена."
            + (" Ставка возвращена." if duel_data.get("wagered") else "")
        )
        return

    if action == "accept":
        if duel_data["status"] != "pending":
            await query.answer("Вызов уже принят.", show_alert=True)
            return
        if duel_data.get("wagered"):
            service = _spy_service(context)
            result = await service.accept_duel_wager(
                duel_id=duel_id,
                user_id=user_id,
                username=callback_username_value,
                display_name=update.effective_user.full_name,
            )
            if result.status is DuelWagerStatus.INSUFFICIENT_AGENTS:
                await query.answer(
                    f"Для принятия нужно {duel_data['stake_amount']} осведомителей.",
                    show_alert=True,
                )
                return
            if result.status is not DuelWagerStatus.CHOOSING:
                await query.answer("Вызов уже недоступен.", show_alert=True)
                return
            _cancel_accept_timeout(context, duel_data)
            timeout_job_name = f"duel-move-timeout:{result.chat_id}:{duel_id}"
            duel_data = _duel_data_from_wager(result)
            duel_data["timeout_job_name"] = timeout_job_name
            duels[duel_id] = duel_data
        await query.answer("Дуэль начинается.")
        if not duel_data.get("wagered"):
            _cancel_accept_timeout(context, duel_data)
        if opponent_id is None:
            duel_data["players"]["opponent"]["id"] = user_id
            duel_data["players"]["opponent"]["name"] = (
                _spy_public_name(update.effective_user)
                if duel_data.get("wagered")
                else _display_name(update.effective_user)
            )
        duel_data["status"] = "choosing"
        await query.edit_message_text(
            _round_text(duel_data), reply_markup=_actions_keyboard(duel_id)
        )
        if duel_data.get("wagered"):
            if context.job_queue is None:
                logger.error(
                    "duel: JobQueue unavailable; move timeout relies on Spy tick"
                )
            else:
                context.job_queue.run_once(
                    duel_move_timeout,
                    service.settings.duel_move_seconds,
                    data={
                        "duel_id": duel_id,
                        "chat_id": update.effective_chat.id,
                        "message_id": query.message.message_id,
                    },
                    name=duel_data["timeout_job_name"],
                    chat_id=update.effective_chat.id,
                )
        return

    if action == "forfeit":
        if duel_data["status"] != "choosing":
            await query.answer("Сейчас нельзя сдаться.", show_alert=True)
            return
        if user_id not in (challenger_id, opponent_id):
            await query.answer("Наблюдатели не могут завершить дуэль.", show_alert=True)
            return
        winner_key = "opponent" if user_id == challenger_id else "challenger"
        winner = duel_data["players"][winner_key]
        if duel_data.get("wagered"):
            result = await _spy_service(context).forfeit_duel_wager(duel_id, user_id)
            if result.status is not DuelWagerStatus.WON:
                await query.answer("Не удалось рассчитать банк.", show_alert=True)
                return
            winner["name"] = result.winner_name
            _cancel_accept_timeout(context, duel_data)
        loser_name = (
            _spy_public_name(update.effective_user)
            if duel_data.get("wagered")
            else _display_name(update.effective_user)
        )
        duels.pop(duel_id, None)
        await query.answer()
        await query.edit_message_text(
            f"{loser_name} сдаётся.\n\nПобедитель: {winner['name']}\n"
            "Гейм-мастер фиксирует техническую победу."
            + (
                f"\nБанк: "
                f"{_agent_amount_text(duel_data['agent_type'], duel_data['stake_amount'] * 2)}."
                if duel_data.get("wagered")
                else ""
            )
        )
        logger.info(
            "duel: forfeited chat=%s winner=%s loser=%s",
            update.effective_chat.id,
            winner["id"],
            user_id,
        )
        return

    if action != "move" or len(parts) != 4 or parts[3] not in _ACTIONS:
        await query.answer("Неизвестный ход.", show_alert=True)
        return
    if duel_data["status"] != "choosing":
        await query.answer("Сейчас нельзя выбирать ход.", show_alert=True)
        return
    if user_id not in (challenger_id, opponent_id):
        await query.answer("Наблюдатели не могут вмешиваться в дуэль.", show_alert=True)
        return

    action_key = parts[3]
    if str(user_id) in duel_data["actions"]:
        await query.answer("Твой ход уже зафиксирован.", show_alert=True)
        return

    wager_result = None
    if duel_data.get("wagered"):
        wager_result = await _spy_service(context).choose_duel_move(
            duel_id=duel_id,
            user_id=user_id,
            action=action_key,
        )
        if wager_result.status is DuelWagerStatus.ALREADY_MOVED:
            await query.answer("Твой ход уже зафиксирован.", show_alert=True)
            return
        if wager_result.status not in {
            DuelWagerStatus.CHOOSING,
            DuelWagerStatus.WON,
        }:
            await query.answer("Ход не принят.", show_alert=True)
            return
        timeout_job_name = duel_data.get("timeout_job_name")
        duel_data = _duel_data_from_wager(wager_result)
        duel_data["timeout_job_name"] = timeout_job_name
        duels[duel_id] = duel_data
    else:
        duel_data["actions"][str(user_id)] = action_key
    await query.answer(f"Ход «{_ACTIONS[action_key][0]}» принят.")

    if len(duel_data["actions"]) < 2:
        await query.edit_message_text(
            _round_text(duel_data), reply_markup=_actions_keyboard(duel_id)
        )
        return

    duel_data["status"] = "judging"
    await query.edit_message_text(
        _round_text(duel_data) + "\n\nГейм-мастер выносит вердикт..."
    )
    if wager_result is not None:
        winner_key = (
            "challenger" if wager_result.winner_user_id == challenger_id else "opponent"
        )
        judgement = await _narrate_wagered_duel(duel_data, winner_key)
        _cancel_accept_timeout(context, duel_data)
    else:
        judgement = await _judge_duel(duel_data)
    winner = duel_data["players"][judgement["winner"]]
    challenger_action = _ACTIONS[duel_data["actions"][str(challenger_id)]][0]
    opponent_action = _ACTIONS[duel_data["actions"][str(opponent_id)]][0]
    duels.pop(duel_id, None)

    await query.edit_message_text(
        f"{duel_data['scenario']['title']}\n\n"
        f"{duel_data['players']['challenger']['name']}: {challenger_action}\n"
        f"{duel_data['players']['opponent']['name']}: {opponent_action}\n\n"
        f"{judgement['narration']}\n\n"
        f"Победитель: {winner['name']}\n"
        f"Вердикт гейм-мастера: {judgement['reason']}"
        + (
            f"\nБанк: "
            f"{_agent_amount_text(duel_data['agent_type'], duel_data['stake_amount'] * 2)}."
            if duel_data.get("wagered")
            else ""
        )
    )
    logger.info(
        "duel: completed chat=%s winner=%s challenger=%s opponent=%s",
        update.effective_chat.id,
        winner["id"],
        challenger_id,
        opponent_id,
    )
