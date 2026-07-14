"""Russian roulette mini-game with one shared chat button."""

import random
import secrets

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import logger
from state import ensure_chat_state

ROULETTE_CALLBACK_PATTERN = r"^roulette:"
ROULETTE_CHAMBERS = 6


def _roulette_store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return ensure_chat_state(context)["roulette_games"]


def _display_name(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name


def _roulette_keyboard(game_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Нажать на курок", callback_data=f"roulette:{game_id}:pull"
                )
            ]
        ]
    )


def _pulls_label(count: int) -> str:
    if 11 <= count % 100 <= 14:
        return f"{count} раз"
    if count % 10 == 1:
        return f"{count} раз"
    if 2 <= count % 10 <= 4:
        return f"{count} раза"
    return f"{count} раз"


def _active_text(game: dict) -> str:
    pulls_left = ROULETTE_CHAMBERS - game["pulls_taken"]
    return (
        "Русская рулетка началась.\n\n"
        f"Нажатий на курок осталось: {pulls_left} из {ROULETTE_CHAMBERS}.\n"
        "Кто нажмёт кнопку, тот делает следующий ход."
    )


def _final_text(game: dict) -> str:
    stats = []
    for user_id in game["player_order"]:
        player = game["players"][str(user_id)]
        stats.append(f"{player['name']}: {_pulls_label(player['pulls'])}")

    stats_text = "\n".join(stats)
    loser = game["loser"]
    return (
        "Русская рулетка завершена.\n\n"
        f"Нажатий сделано: {game['pulls_taken']} из {ROULETTE_CHAMBERS}.\n"
        f"Проиграл: {loser['name']}.\n\n"
        f"Статистика:\n{stats_text}"
    )


async def start_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a Russian roulette game for the current chat."""
    source_message = update.message
    if source_message is None and update.callback_query is not None:
        source_message = update.callback_query.message
    if source_message is None:
        return

    games = _roulette_store(context)
    if games:
        await source_message.reply_text(
            "Рулетка уже крутится. Нажимайте кнопку в активной игре."
        )
        return

    game_id = secrets.token_hex(4)
    game = {
        "id": game_id,
        "bullet_chamber": random.randint(1, ROULETTE_CHAMBERS),
        "pulls_taken": 0,
        "players": {},
        "player_order": [],
        "loser": None,
    }
    games[game_id] = game

    await source_message.reply_text(
        _active_text(game), reply_markup=_roulette_keyboard(game_id)
    )
    logger.info(
        "roulette: started chat=%s game=%s bullet_chamber=%s",
        update.effective_chat.id,
        game_id,
        game["bullet_chamber"],
    )


async def roulette_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a trigger pull and resolve the roulette game when the chamber fires."""
    query = update.callback_query
    if query is None or update.effective_user is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[2] != "pull":
        await query.answer("Некорректная команда рулетки.", show_alert=True)
        return

    game_id = parts[1]
    games = _roulette_store(context)
    game = games.get(game_id)
    if game is None:
        await query.answer(
            "Эта рулетка уже завершена или потеряна после перезапуска.",
            show_alert=True,
        )
        return

    user_id = update.effective_user.id
    player_key = str(user_id)
    if player_key not in game["players"]:
        game["players"][player_key] = {
            "id": user_id,
            "name": _display_name(update.effective_user),
            "pulls": 0,
        }
        game["player_order"].append(user_id)

    game["pulls_taken"] += 1
    game["players"][player_key]["pulls"] += 1

    if game["pulls_taken"] == game["bullet_chamber"]:
        game["loser"] = game["players"][player_key]
        games.pop(game_id, None)
        await query.answer("Бах.")
        await query.edit_message_text(_final_text(game))
        logger.info(
            "roulette: completed chat=%s game=%s loser=%s pulls=%s",
            update.effective_chat.id,
            game_id,
            user_id,
            game["pulls_taken"],
        )
        return

    await query.answer("Щёлк. Пусто.")
    await query.edit_message_text(
        _active_text(game), reply_markup=_roulette_keyboard(game_id)
    )
