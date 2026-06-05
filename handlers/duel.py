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
from state import ensure_chat_state, remember_chat_user
from telegram_utils import parse_stream

DUEL_CALLBACK_PATTERN = r"^duel:"
DUEL_MODEL = "deepseek-v4-flash"
DUEL_ACCEPT_TIMEOUT_SECONDS = 120

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
            source = source[start : end + 1]
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
        if all(isinstance(result.get(key), str) for key in ("title", "setting", "condition")):
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
                InlineKeyboardButton("Принять вызов", callback_data=f"duel:{duel_id}:accept"),
                InlineKeyboardButton("Отступить", callback_data=f"duel:{duel_id}:decline"),
            ],
            [InlineKeyboardButton("Отменить вызов", callback_data=f"duel:{duel_id}:cancel")],
        ]
    )


def _actions_keyboard(duel_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚔️ Атаковать", callback_data=f"duel:{duel_id}:move:attack"),
                InlineKeyboardButton("🛡 Защищаться", callback_data=f"duel:{duel_id}:move:defend"),
            ],
            [
                InlineKeyboardButton("🎭 Обмануть", callback_data=f"duel:{duel_id}:move:trick"),
                InlineKeyboardButton(
                    "🔎 Окружение", callback_data=f"duel:{duel_id}:move:environment"
                ),
            ],
            [
                InlineKeyboardButton(
                    "💥 Рискованный манёвр", callback_data=f"duel:{duel_id}:move:risk"
                )
            ],
            [InlineKeyboardButton("🏳 Сдаться", callback_data=f"duel:{duel_id}:forfeit")],
        ]
    )


def _challenge_text(duel: dict) -> str:
    challenger = duel["players"]["challenger"]["name"]
    opponent = duel["players"]["opponent"]["name"]
    scenario = duel["scenario"]
    return (
        f"{scenario['title']}\n\n"
        f"{scenario['setting']}\n\n"
        f"Условие победы: {scenario['condition']}\n\n"
        f"{challenger} вызывает {opponent} на дуэль профессионалов.\n"
        "Только вызванный дуэлянт может принять или отклонить вызов.\n"
        f"На принятие вызова даётся {DUEL_ACCEPT_TIMEOUT_SECONDS} секунд."
    )


def _round_text(duel: dict) -> str:
    challenger = duel["players"]["challenger"]
    opponent = duel["players"]["opponent"]
    selected = duel["actions"]

    def status(player: dict) -> str:
        return "ход выбран" if str(player["id"]) in selected else "ожидается ход"

    return (
        f"{duel['scenario']['title']}\n\n"
        f"{duel['scenario']['setting']}\n\n"
        f"Условие победы: {duel['scenario']['condition']}\n\n"
        "Гейм-мастер наблюдает. Выберите действия тайно:\n"
        f"• {challenger['name']}: {status(challenger)}\n"
        f"• {opponent['name']}: {status(opponent)}"
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
            return ensure_chat_state(context)["known_chat_users"].get(username) or username
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

    duels.pop(duel_id, None)
    text = (
        f"{duel_data['players']['opponent']['name']} не принял вызов за "
        f"{DUEL_ACCEPT_TIMEOUT_SECONDS} секунд.\n\n"
        "Гейм-мастер закрывает дело. Время вышло."
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


@pause
async def duel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a duel challenge against a mentioned or replied-to user."""
    if update.message is None or update.effective_user is None:
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
        await update.message.reply_text("В этом чате уже идёт дуэль. Дождитесь её завершения.")
        return

    duel_id = secrets.token_hex(4)
    challenger_data = {
        "id": update.effective_user.id,
        "name": _display_name(update.effective_user),
    }
    if isinstance(opponent, str):
        if update.effective_user.username and opponent == update.effective_user.username.lower():
            await update.message.reply_text("Дуэль с самим собой отменена медицинской комиссией.")
            return
        opponent_data = {"id": None, "name": f"@{opponent}", "username": opponent}
    else:
        if opponent.id == update.effective_user.id:
            await update.message.reply_text("Дуэль с самим собой отменена медицинской комиссией.")
            return
        if opponent.is_bot:
            await update.message.reply_text("Другие боты не допущены к профессиональным дуэлям.")
            return
        opponent_data = {
            "id": opponent.id,
            "name": _display_name(opponent),
            "username": opponent.username.lower() if opponent.username else None,
        }
    scenario = await _generate_scenario(challenger_data["name"], opponent_data["name"])
    duel_data = {
        "id": duel_id,
        "status": "pending",
        "timeout_job_name": f"duel-accept-timeout:{update.effective_chat.id}:{duel_id}",
        "players": {"challenger": challenger_data, "opponent": opponent_data},
        "scenario": scenario,
        "actions": {},
    }
    duels[duel_id] = duel_data
    challenge_message = await update.message.reply_text(
        _challenge_text(duel_data), reply_markup=_challenge_keyboard(duel_id)
    )
    if context.job_queue is None:
        logger.error("duel: JobQueue is unavailable; acceptance timeout was not scheduled")
    else:
        context.job_queue.run_once(
            duel_accept_timeout,
            DUEL_ACCEPT_TIMEOUT_SECONDS,
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
    if update.message is None or update.effective_user is None:
        return

    duels = _duel_store(context)
    if not duels:
        await update.message.reply_text("В этом чате нет активной дуэли.")
        return

    duel_id, duel_data = next(iter(duels.items()))
    participant_ids = {
        duel_data["players"]["challenger"]["id"],
        duel_data["players"]["opponent"]["id"],
    }
    allowed_ids = participant_ids | {context.bot_data["master"]}
    if update.effective_user.id not in allowed_ids:
        await update.message.reply_text("Отменить эту дуэль могут только её участники.")
        return

    _cancel_accept_timeout(context, duel_data)
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
        await query.answer("Эта дуэль уже завершена или потеряна после перезапуска.", show_alert=True)
        return

    challenger_id = duel_data["players"]["challenger"]["id"]
    opponent_id = duel_data["players"]["opponent"]["id"]
    user_id = update.effective_user.id
    opponent_username = duel_data["players"]["opponent"].get("username")
    callback_username_value = getattr(update.effective_user, "username", None)
    callback_username = callback_username_value.lower() if callback_username_value else None
    is_opponent = user_id == opponent_id or (
        opponent_id is None and callback_username == opponent_username
    )

    if action in ("accept", "decline") and not is_opponent:
        await query.answer("Эта кнопка предназначена только вызванному дуэлянту.", show_alert=True)
        return
    if action == "cancel" and user_id != challenger_id:
        await query.answer("Отменить вызов может только его автор.", show_alert=True)
        return

    if action == "decline":
        await query.answer()
        _cancel_accept_timeout(context, duel_data)
        duels.pop(duel_id, None)
        await query.edit_message_text(
            f"{duel_data['players']['opponent']['name']} отказывается от дуэли. C'est la vie."
        )
        return

    if action == "cancel":
        await query.answer()
        _cancel_accept_timeout(context, duel_data)
        duels.pop(duel_id, None)
        await query.edit_message_text("Вызов отозван. Операция отменена.")
        return

    if action == "accept":
        if duel_data["status"] != "pending":
            await query.answer("Вызов уже принят.", show_alert=True)
            return
        await query.answer("Дуэль начинается.")
        _cancel_accept_timeout(context, duel_data)
        if opponent_id is None:
            duel_data["players"]["opponent"]["id"] = user_id
            duel_data["players"]["opponent"]["name"] = _display_name(update.effective_user)
        duel_data["status"] = "choosing"
        await query.edit_message_text(
            _round_text(duel_data), reply_markup=_actions_keyboard(duel_id)
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
        loser_name = _display_name(update.effective_user)
        duels.pop(duel_id, None)
        await query.answer()
        await query.edit_message_text(
            f"{loser_name} сдаётся.\n\nПобедитель: {winner['name']}\n"
            "Гейм-мастер фиксирует техническую победу."
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
    )
    logger.info(
        "duel: completed chat=%s winner=%s challenger=%s opponent=%s",
        update.effective_chat.id,
        winner["id"],
        challenger_id,
        opponent_id,
    )
