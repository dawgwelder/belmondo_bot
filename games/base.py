"""Lifecycle helpers shared by all LLM group games."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from state import ensure_chat_state

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

MIN_PLAYERS = 2
MAX_PLAYERS = 8
LOBBY_TIMEOUT_SECONDS = 180
ROUND_TIMEOUT_SECONDS = 180
MAX_MOVE_LENGTH = 500
GAME_CALLBACK_PREFIX = "game"


def display_name(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    return user.full_name


def game_store(context: "ContextTypes.DEFAULT_TYPE") -> dict[str, dict[str, Any]]:
    return ensure_chat_state(context)["llm_games"]


def get_active_game(
    context: "ContextTypes.DEFAULT_TYPE",
) -> tuple[str, dict[str, Any]] | None:
    games = game_store(context)
    if not games:
        return None
    game_id, game = next(iter(games.items()))
    return game_id, game


def player_entries(game: dict[str, Any]) -> list[dict[str, Any]]:
    return [game["players"][str(key)] for key in game["player_order"]]


def player_names(game: dict[str, Any]) -> str:
    return ", ".join(player["name"] for player in player_entries(game))


def roster_text(game: dict[str, Any]) -> str:
    lines = [f"• {player['name']}" for player in player_entries(game)]
    return "\n".join(lines)


def add_player(game: dict[str, Any], user) -> bool:
    key = str(user.id)
    if key in game["players"]:
        return False
    if len(game["players"]) >= MAX_PLAYERS:
        return False
    game["players"][key] = {"id": user.id, "name": display_name(user)}
    game["player_order"].append(user.id)
    return True


def is_player(game: dict[str, Any], user_id: int) -> bool:
    return str(user_id) in game["players"]


def move_key(round_num: int) -> str:
    return str(round_num)


def has_move(game: dict[str, Any], user_id: int, round_num: int) -> bool:
    return move_key(round_num) in game["moves"].get(str(user_id), {})


def record_move(game: dict[str, Any], user_id: int, round_num: int, text: str) -> bool:
    if has_move(game, user_id, round_num):
        return False
    compact = " ".join(text.strip().split())[:MAX_MOVE_LENGTH]
    game["moves"].setdefault(str(user_id), {})[move_key(round_num)] = compact
    return True


def submitted_player_ids(game: dict[str, Any], round_num: int) -> list[int]:
    submitted: list[int] = []
    for user_id in game["player_order"]:
        if has_move(game, user_id, round_num):
            submitted.append(user_id)
    return submitted


def active_player_ids(game: dict[str, Any]) -> list[int]:
    return list(game.get("active_player_ids") or game["player_order"])


def pending_player_ids(game: dict[str, Any], round_num: int) -> list[int]:
    active = set(active_player_ids(game))
    return [
        user_id
        for user_id in game["player_order"]
        if user_id in active and not has_move(game, user_id, round_num)
    ]


def all_moves_submitted(game: dict[str, Any], round_num: int) -> bool:
    return not pending_player_ids(game, round_num)


def participants_for_judging(game: dict[str, Any], round_num: int) -> list[dict[str, Any]]:
    submitted = {user_id for user_id in submitted_player_ids(game, round_num)}
    return [player for player in player_entries(game) if player["id"] in submitted]


def move_lines(game: dict[str, Any], round_num: int) -> str:
    lines: list[str] = []
    for player in player_entries(game):
        move = game["moves"].get(str(player["id"]), {}).get(move_key(round_num))
        if move:
            lines.append(f"{player['name']}: {move}")
    return "\n".join(lines)


def cancel_timeout(context: "ContextTypes.DEFAULT_TYPE", game: dict[str, Any]) -> None:
    job_queue = getattr(context, "job_queue", None)
    timeout_job_name = game.get("timeout_job_name")
    if job_queue is None or timeout_job_name is None:
        return
    for job in job_queue.get_jobs_by_name(timeout_job_name):
        job.schedule_removal()
    game["timeout_job_name"] = None


def schedule_timeout(
    context: "ContextTypes.DEFAULT_TYPE",
    *,
    game: dict[str, Any],
    callback,
    delay: int,
    data: dict[str, Any],
) -> None:
    cancel_timeout(context, game)
    job_queue = getattr(context, "job_queue", None)
    if job_queue is None:
        return
    job_name = f"game-timeout:{game['chat_id']}:{game['id']}:{game['status']}:{game.get('round', 0)}"
    game["timeout_job_name"] = job_name
    job_queue.run_once(
        callback,
        delay,
        data=data,
        name=job_name,
        chat_id=game["chat_id"],
    )


def build_callback(game_type: str, game_id: str, action: str) -> str:
    return f"{GAME_CALLBACK_PREFIX}:{game_type}:{game_id}:{action}"


def parse_callback(data: str) -> tuple[str, str, str] | None:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != GAME_CALLBACK_PREFIX:
        return None
    return parts[1], parts[2], parts[3]
