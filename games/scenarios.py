"""Scenario definitions for LLM group games."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from games.base import (
    participants_for_judging,
    player_entries,
    submitted_player_ids,
    active_player_ids,
)
from games.llm import compact, request_json, untrusted_json_block


def _player_id_map(game: dict[str, Any]) -> dict[str, int]:
    return {str(player["id"]): player["id"] for player in player_entries(game)}


def _players_payload(game: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"user_id": player["id"], "name": player["name"]}
        for player in player_entries(game)
    ]


def _answers_payload(game: dict[str, Any], round_num: int) -> dict[str, str]:
    result: dict[str, str] = {}
    for player in player_entries(game):
        move = game["moves"].get(str(player["id"]), {}).get(str(round_num))
        if move:
            result[str(player["id"])] = move
    return result


def _strict_fields(payload: dict[str, Any], expected: set[str]) -> bool:
    return set(payload) == expected


def _valid_int_id(value: Any, valid_ids: set[int]) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value in valid_ids


def _validate_player_id_map(
    payload: dict[str, Any],
    game: dict[str, Any],
    field: str,
    *,
    allowed_ids: set[str] | None = None,
) -> dict[str, str] | None:
    raw = payload.get(field)
    if not isinstance(raw, dict):
        return None
    valid_ids = allowed_ids or set(_player_id_map(game))
    result: dict[str, str] = {}
    for key, value in raw.items():
        if str(key) not in valid_ids or not isinstance(value, str) or not value.strip():
            return None
        result[str(key)] = compact(value, 400)
    if set(result) != valid_ids:
        return None
    return result


def _validate_nominations(
    value: Any, valid_ids: set[int]
) -> list[dict[str, Any]] | None:
    if not isinstance(value, list):
        return None
    cleaned = []
    for item in value[:3]:
        if not isinstance(item, dict):
            return None
        player_id = item.get("player_id")
        if (
            not _strict_fields(item, {"player_id", "title", "reason"})
            or not _valid_int_id(player_id, valid_ids)
            or not isinstance(item.get("title"), str)
            or not isinstance(item.get("reason"), str)
        ):
            return None
        cleaned.append(
            {
                "player_id": player_id,
                "title": compact(item["title"], 120),
                "reason": compact(item["reason"], 200),
            }
        )
    return cleaned


def _validate_winner_verdict(
    payload: dict[str, Any], valid_ids: set[int]
) -> dict[str, Any] | None:
    winner_id = payload.get("winner_id")
    if (
        not _strict_fields(payload, {"analysis", "winner_id", "nominations"})
        or not isinstance(payload.get("analysis"), str)
        or not _valid_int_id(winner_id, valid_ids)
    ):
        return None
    nominations = _validate_nominations(payload.get("nominations"), valid_ids)
    if nominations is None:
        return None
    return {
        "analysis": compact(payload["analysis"], 1800),
        "winner_id": winner_id,
        "nominations": nominations,
    }


def _format_nominations(game: dict[str, Any], verdict: dict[str, Any]) -> str:
    players_by_id = {player["id"]: player["name"] for player in player_entries(game)}
    lines = "\n".join(
        f"• {players_by_id[item['player_id']]} — {item['title']}: {item['reason']}"
        for item in verdict.get("nominations", [])
    )
    return f"\n\nНоминации:\n{lines}" if lines else ""


@dataclass(frozen=True)
class GameScenario:
    game_type: str
    title: str
    command: str
    lobby_intro: str

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError

    def format_opening_message(self, game: dict[str, Any], opening: dict[str, Any]) -> str:
        raise NotImplementedError

    def format_round_two_message(self, game: dict[str, Any], payload: dict[str, Any]) -> str:
        raise NotImplementedError

    def format_verdict_message(self, game: dict[str, Any], verdict: dict[str, Any]) -> str:
        raise NotImplementedError


class AlibiScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="alibi",
            title="Алиби, месье",
            command="/game",
            lobby_intro="Расследование начинается. Наберите минимум двух агентов.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер шуточной групповой игры «Алиби, месье». "
            "Придумай кинематографичное происшествие в духе французского боевика "
            "и один общий вопрос про алиби для всех игроков. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"incident":"2-3 предложения","common_question":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"incident", "common_question"}):
                return None
            if not all(isinstance(payload.get(key), str) for key in ("incident", "common_question")):
                return None
            return {
                "incident": compact(payload["incident"], 900),
                "common_question": compact(payload["common_question"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint='Нужны строки incident и common_question.',
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        active_ids = {str(player_id) for player_id in submitted_player_ids(game, 1)}
        payload = {
            "incident": opening,
            "players": [
                player
                for player in _players_payload(game)
                if str(player["user_id"]) in active_ids
            ],
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер игры «Алиби, месье». "
            "Сформулируй персональный уточняющий вопрос каждому игроку, "
            "исходя из его ответа. Ключи в questions — строковые player_id.\n"
            f"Допустимые player_id: {', '.join(sorted(active_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"questions":{"123":"вопрос"}}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            questions = _validate_player_id_map(
                payload, game, "questions", allowed_ids=active_ids
            )
            if questions is None:
                return None
            return {"questions": questions}

        return await request_json(
            prompt,
            validate,
            corrective_hint="questions должен содержать ровно одну строку на каждого player_id.",
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        questions = game["content"]["round_two"]["questions"]
        participants = participants_for_judging(game, 2) or participants_for_judging(game, 1)
        valid_ids = {player["id"] for player in participants}
        payload = {
            "incident": opening,
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "followup_questions": questions,
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты беспристрастный гейм-мастер «Алиби, месье». "
            "Выбери самое убедительное алиби, главного подозреваемого и 1-2 короткие номинации. "
            "Не объявляй ничью; winner_id и suspect_id должны быть разными, если игроков больше одного. "
            "winner_id и suspect_id — числовые id участников из списка. "
            f"Допустимые id: {', '.join(str(player_id) for player_id in valid_ids)}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"suspect_id":456,"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            suspect_id = payload.get("suspect_id")
            if (
                not _strict_fields(payload, {"analysis", "winner_id", "suspect_id", "nominations"})
                or not _valid_int_id(winner_id, valid_ids)
                or not _valid_int_id(suspect_id, valid_ids)
                or (len(valid_ids) > 1 and winner_id == suspect_id)
                or not isinstance(payload.get("analysis"), str)
            ):
                return None
            nominations = payload.get("nominations")
            if not isinstance(nominations, list):
                return None
            cleaned = []
            for item in nominations[:3]:
                if not isinstance(item, dict):
                    return None
                player_id = item.get("player_id")
                if player_id not in valid_ids:
                    return None
                if not isinstance(item.get("title"), str) or not isinstance(item.get("reason"), str):
                    return None
                cleaned.append(
                    {
                        "player_id": player_id,
                        "title": compact(item["title"], 120),
                        "reason": compact(item["reason"], 200),
                    }
                )
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "suspect_id": suspect_id,
                "nominations": cleaned,
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="analysis — строка, winner_id/suspect_id — id участников, nominations — массив.",
        )

    def format_opening_message(self, game: dict[str, Any], opening: dict[str, Any]) -> str:
        return (
            f"{self.title}\n\n"
            f"{opening['incident']}\n\n"
            f"Вопрос раунда 1: {opening['common_question']}\n\n"
            "Ответьте reply на это сообщение своим алиби."
        )

    def format_round_two_message(self, game: dict[str, Any], payload: dict[str, Any]) -> str:
        active_ids = set(active_player_ids(game))
        lines = [
            f"{player['name']}: {payload['questions'][str(player['id'])]}"
            for player in player_entries(game)
            if player["id"] in active_ids
        ]
        return (
            f"{self.title} — раунд 2\n\n"
            + "\n".join(lines)
            + "\n\nОтветьте reply на это сообщение."
        )

    def format_verdict_message(self, game: dict[str, Any], verdict: dict[str, Any]) -> str:
        players_by_id = {player["id"]: player["name"] for player in player_entries(game)}
        nominations = "\n".join(
            f"• {players_by_id[item['player_id']]} — {item['title']}: {item['reason']}"
            for item in verdict.get("nominations", [])
        )
        extra = f"\n\nНоминации:\n{nominations}" if nominations else ""
        return (
            f"{self.title} — вердикт\n\n"
            f"{verdict['analysis']}\n\n"
            f"Самое убедительное алиби: {players_by_id[verdict['winner_id']]}\n"
            f"Главный подозреваемый: {players_by_id[verdict['suspect_id']]}"
            f"{extra}"
        )


class OperationScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="operation",
            title="Невозможная операция",
            command="/game",
            lobby_intro="Штаб собирает профессионалов для абсурдной операции.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер шуточной шпионской игры «Невозможная операция». "
            "Придумай абсурдную операцию и задание первого раунда — каждый игрок предложит план. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"operation":"2-3 предложения","round_one_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"operation", "round_one_task"}):
                return None
            if not all(isinstance(payload.get(key), str) for key in ("operation", "round_one_task")):
                return None
            return {
                "operation": compact(payload["operation"], 900),
                "round_one_task": compact(payload["round_one_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint='Нужны строки operation и round_one_task.',
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        payload = {
            "operation": opening,
            "players": _players_payload(game),
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер «Невозможная операция». "
            "Придумай одно общее неожиданное осложнение и задание второго раунда — "
            "как каждый адаптирует свой план. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"twist":"2 предложения","round_two_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"twist", "round_two_task"}):
                return None
            if not all(isinstance(payload.get(key), str) for key in ("twist", "round_two_task")):
                return None
            return {
                "twist": compact(payload["twist"], 700),
                "round_two_task": compact(payload["round_two_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint='Нужны строки twist и round_two_task.',
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        twist = game["content"]["round_two"]
        participants = participants_for_judging(game, 2) or participants_for_judging(game, 1)
        valid_ids = {player["id"] for player in participants}
        payload = {
            "operation": opening,
            "twist": twist,
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты гейм-мастер «Невозможная операция». "
            "Опиши последствия и выбери лучший план. winner_id — id победителя. "
            f"Допустимые id: {', '.join(str(player_id) for player_id in valid_ids)}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            if (
                not _strict_fields(payload, {"analysis", "winner_id", "nominations"})
                or not _valid_int_id(winner_id, valid_ids)
                or not isinstance(payload.get("analysis"), str)
            ):
                return None
            nominations = payload.get("nominations")
            if not isinstance(nominations, list):
                return None
            cleaned = []
            for item in nominations[:3]:
                if not isinstance(item, dict):
                    return None
                player_id = item.get("player_id")
                if player_id not in valid_ids:
                    return None
                if not isinstance(item.get("title"), str) or not isinstance(item.get("reason"), str):
                    return None
                cleaned.append(
                    {
                        "player_id": player_id,
                        "title": compact(item["title"], 120),
                        "reason": compact(item["reason"], 200),
                    }
                )
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "nominations": cleaned,
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="analysis — строка, winner_id — id одного из участников.",
        )

    def format_opening_message(self, game: dict[str, Any], opening: dict[str, Any]) -> str:
        return (
            f"{self.title}\n\n"
            f"{opening['operation']}\n\n"
            f"Задание раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение своим планом."
        )

    def format_round_two_message(self, game: dict[str, Any], payload: dict[str, Any]) -> str:
        return (
            f"{self.title} — раунд 2\n\n"
            f"Осложнение: {payload['twist']}\n\n"
            f"Задание: {payload['round_two_task']}\n\n"
            "Ответьте reply на это сообщение адаптированным планом."
        )

    def format_verdict_message(self, game: dict[str, Any], verdict: dict[str, Any]) -> str:
        players_by_id = {player["id"]: player["name"] for player in player_entries(game)}
        nominations = "\n".join(
            f"• {players_by_id[item['player_id']]} — {item['title']}: {item['reason']}"
            for item in verdict.get("nominations", [])
        )
        extra = f"\n\nНоминации:\n{nominations}" if nominations else ""
        return (
            f"{self.title} — вердикт\n\n"
            f"{verdict['analysis']}\n\n"
            f"Лучший план: {players_by_id[verdict['winner_id']]}{extra}"
        )


class PitchScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="pitch",
            title="Продай это Бельмондо",
            command="/game",
            lobby_intro="Клиент требователен, товар — абсурден. Готовьте питчи.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер игры «Продай это Бельмондо». "
            "Придумай один нелепый предмет и роль требовательного клиента. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"item":"1-2 предложения","client":"1-2 предложения",'
            '"round_one_task":"задание для короткого питча"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            keys = ("item", "client", "round_one_task")
            if not _strict_fields(payload, set(keys)):
                return None
            if not all(isinstance(payload.get(key), str) for key in keys):
                return None
            return {key: compact(payload[key], 500 if key != "round_one_task" else 400) for key in keys}

        return await request_json(
            prompt,
            validate,
            corrective_hint="Нужны строки item, client и round_one_task.",
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        payload = {
            "pitch_brief": opening,
            "players": _players_payload(game),
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер «Продай это Бельмондо». "
            "Клиент добавляет новое жёсткое требование для второго раунда. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"new_requirement":"1-2 предложения","round_two_task":"задание"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"new_requirement", "round_two_task"}):
                return None
            if not all(isinstance(payload.get(key), str) for key in ("new_requirement", "round_two_task")):
                return None
            return {
                "new_requirement": compact(payload["new_requirement"], 500),
                "round_two_task": compact(payload["round_two_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="Нужны строки new_requirement и round_two_task.",
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        opening = game["content"]["opening"]
        requirement = game["content"]["round_two"]
        participants = participants_for_judging(game, 2) or participants_for_judging(game, 1)
        valid_ids = {player["id"] for player in participants}
        payload = {
            "pitch_brief": opening,
            "new_requirement": requirement,
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты гейм-мастер «Продай это Бельмондо». "
            "Выбери победителя и объясни, чей товар появился бы во французском боевике. "
            f"Допустимые id: {', '.join(str(player_id) for player_id in valid_ids)}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            if (
                not _strict_fields(payload, {"analysis", "winner_id", "nominations"})
                or not _valid_int_id(winner_id, valid_ids)
                or not isinstance(payload.get("analysis"), str)
            ):
                return None
            nominations = payload.get("nominations")
            if not isinstance(nominations, list):
                return None
            cleaned = []
            for item in nominations[:3]:
                if not isinstance(item, dict):
                    return None
                player_id = item.get("player_id")
                if player_id not in valid_ids:
                    return None
                if not isinstance(item.get("title"), str) or not isinstance(item.get("reason"), str):
                    return None
                cleaned.append(
                    {
                        "player_id": player_id,
                        "title": compact(item["title"], 120),
                        "reason": compact(item["reason"], 200),
                    }
                )
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "nominations": cleaned,
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="analysis — строка, winner_id — id одного из участников.",
        )

    def format_opening_message(self, game: dict[str, Any], opening: dict[str, Any]) -> str:
        return (
            f"{self.title}\n\n"
            f"Товар: {opening['item']}\n"
            f"Клиент: {opening['client']}\n\n"
            f"Задание раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение коротким питчем."
        )

    def format_round_two_message(self, game: dict[str, Any], payload: dict[str, Any]) -> str:
        return (
            f"{self.title} — раунд 2\n\n"
            f"Новое требование клиента: {payload['new_requirement']}\n\n"
            f"Задание: {payload['round_two_task']}\n\n"
            "Ответьте reply на это сообщение обновлённым питчем."
        )

    def format_verdict_message(self, game: dict[str, Any], verdict: dict[str, Any]) -> str:
        players_by_id = {player["id"]: player["name"] for player in player_entries(game)}
        nominations = "\n".join(
            f"• {players_by_id[item['player_id']]} — {item['title']}: {item['reason']}"
            for item in verdict.get("nominations", [])
        )
        extra = f"\n\nНоминации:\n{nominations}" if nominations else ""
        return (
            f"{self.title} — вердикт\n\n"
            f"{verdict['analysis']}\n\n"
            f"Победитель: {players_by_id[verdict['winner_id']]}{extra}"
        )


class ChaseScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="chase",
            title="Погоня на чём попало",
            command="/game",
            lobby_intro="Обычный транспорт отменяется. Нужны самые находчивые водители.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер шуточной игры «Погоня на чём попало». "
            "Придумай зрелищную вымышленную погоню в духе французской экшен-комедии: "
            "цель, место действия и смешную причину, по которой обычный транспорт недоступен. "
            "Игроки сами выберут нелепый транспорт и предложат план перехвата. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"mission":"2-3 предложения","location":"место",'
            '"transport_rule":"почему обычный транспорт недоступен",'
            '"round_one_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            keys = ("mission", "location", "transport_rule", "round_one_task")
            if not _strict_fields(payload, set(keys)):
                return None
            if not all(isinstance(payload.get(key), str) for key in keys):
                return None
            return {
                "mission": compact(payload["mission"], 800),
                "location": compact(payload["location"], 250),
                "transport_rule": compact(payload["transport_rule"], 400),
                "round_one_task": compact(payload["round_one_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "Нужны строки mission, location, transport_rule и round_one_task."
            ),
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {
            "chase": game["content"]["opening"],
            "players": _players_payload(game),
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер «Погони на чём попало». "
            "На основе планов игроков придумай одно общее неожиданное осложнение, "
            "которое не обесценивает ни один ответ, и попроси всех адаптировать погоню. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"twist":"2 предложения","round_two_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"twist", "round_two_task"}):
                return None
            if not all(
                isinstance(payload.get(key), str) for key in ("twist", "round_two_task")
            ):
                return None
            return {
                "twist": compact(payload["twist"], 700),
                "round_two_task": compact(payload["round_two_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="Нужны строки twist и round_two_task.",
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        participants = participants_for_judging(game, 2) or participants_for_judging(
            game, 1
        )
        valid_ids = {player["id"] for player in participants}
        payload = {
            "chase": game["content"]["opening"],
            "twist": game["content"]["round_two"],
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты гейм-мастер «Погони на чём попало». "
            "Опиши короткий кинематографичный финал, выбери самый находчивый план "
            "и выдай до трёх смешных номинаций. Не объявляй ничью. "
            f"Допустимые id: {', '.join(str(value) for value in sorted(valid_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )
        return await request_json(
            prompt,
            lambda value: _validate_winner_verdict(value, valid_ids),
            corrective_hint=(
                "analysis — строка, winner_id — id участника, nominations — массив."
            ),
        )

    def format_opening_message(
        self, game: dict[str, Any], opening: dict[str, Any]
    ) -> str:
        return (
            f"{self.title}\n\n"
            f"Миссия: {opening['mission']}\n"
            f"Место: {opening['location']}\n"
            f"Ограничение: {opening['transport_rule']}\n\n"
            f"Задание раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение своим планом погони."
        )

    def format_round_two_message(
        self, game: dict[str, Any], payload: dict[str, Any]
    ) -> str:
        return (
            f"{self.title} — раунд 2\n\n"
            f"Осложнение: {payload['twist']}\n\n"
            f"Задание: {payload['round_two_task']}\n\n"
            "Ответьте reply на это сообщение обновлённым планом."
        )

    def format_verdict_message(
        self, game: dict[str, Any], verdict: dict[str, Any]
    ) -> str:
        players_by_id = {
            player["id"]: player["name"] for player in player_entries(game)
        }
        return (
            f"{self.title} — финиш\n\n"
            f"{verdict['analysis']}\n\n"
            f"Победитель погони: {players_by_id[verdict['winner_id']]}"
            f"{_format_nominations(game, verdict)}"
        )


class VillainCastingScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="villain_casting",
            title="Кастинг злодеев",
            command="/game",
            lobby_intro="Киностудия ищет антагониста, которого невозможно забыть.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер шуточной игры «Кастинг злодеев». "
            "Придумай французский боевик, нелепую цель главного злодея и обязательный "
            "предмет его образа. Игроки представят имя злодея, способность, слабость "
            "и реплику для трейлера. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"movie":"название и синопсис в 2 предложениях",'
            '"villain_goal":"цель","required_prop":"предмет",'
            '"round_one_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            keys = ("movie", "villain_goal", "required_prop", "round_one_task")
            if not _strict_fields(payload, set(keys)):
                return None
            if not all(isinstance(payload.get(key), str) for key in keys):
                return None
            return {
                "movie": compact(payload["movie"], 700),
                "villain_goal": compact(payload["villain_goal"], 400),
                "required_prop": compact(payload["required_prop"], 250),
                "round_one_task": compact(payload["round_one_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "Нужны строки movie, villain_goal, required_prop и round_one_task."
            ),
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        active_ids = {str(player_id) for player_id in submitted_player_ids(game, 1)}
        payload = {
            "casting": game["content"]["opening"],
            "players": [
                player
                for player in _players_payload(game)
                if str(player["user_id"]) in active_ids
            ],
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер «Кастинга злодеев». "
            "Дай каждому игроку одно персональное экранное испытание, используя его "
            "способность, слабость или реплику из первого ответа. Испытание должно "
            "быть коротким, смешным и отличаться от испытаний остальных. "
            "Ключи challenges — строковые player_id.\n"
            f"Допустимые player_id: {', '.join(sorted(active_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"challenges":{"123":"испытание"}}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            challenges = _validate_player_id_map(
                payload, game, "challenges", allowed_ids=active_ids
            )
            if challenges is None:
                return None
            return {"challenges": challenges}

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "challenges должен содержать ровно одну строку на каждого player_id."
            ),
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        participants = participants_for_judging(game, 2) or participants_for_judging(
            game, 1
        )
        valid_ids = {player["id"] for player in participants}
        payload = {
            "casting": game["content"]["opening"],
            "challenges": game["content"]["round_two"]["challenges"],
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты режиссёр и гейм-мастер «Кастинга злодеев». "
            "Выбери исполнителя главной роли, объясни решение, придумай короткую сцену "
            "после титров и выдай до трёх смешных номинаций. Не объявляй ничью. "
            f"Допустимые id: {', '.join(str(value) for value in sorted(valid_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"post_credits_scene":"1-2 предложения",'
            '"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            if (
                not _strict_fields(
                    payload,
                    {"analysis", "winner_id", "post_credits_scene", "nominations"},
                )
                or not isinstance(payload.get("analysis"), str)
                or not isinstance(payload.get("post_credits_scene"), str)
                or not _valid_int_id(winner_id, valid_ids)
            ):
                return None
            nominations = _validate_nominations(payload.get("nominations"), valid_ids)
            if nominations is None:
                return None
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "post_credits_scene": compact(payload["post_credits_scene"], 700),
                "nominations": nominations,
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "Нужны analysis, winner_id, post_credits_scene и nominations."
            ),
        )

    def format_opening_message(
        self, game: dict[str, Any], opening: dict[str, Any]
    ) -> str:
        return (
            f"{self.title}\n\n"
            f"Фильм: {opening['movie']}\n"
            f"Цель злодея: {opening['villain_goal']}\n"
            f"Обязательный реквизит: {opening['required_prop']}\n\n"
            f"Задание раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение своей заявкой на роль."
        )

    def format_round_two_message(
        self, game: dict[str, Any], payload: dict[str, Any]
    ) -> str:
        active_ids = set(active_player_ids(game))
        lines = [
            f"{player['name']}: {payload['challenges'][str(player['id'])]}"
            for player in player_entries(game)
            if player["id"] in active_ids
        ]
        return (
            f"{self.title} — экранные пробы\n\n"
            + "\n".join(lines)
            + "\n\nОтветьте reply на это сообщение, не выходя из образа."
        )

    def format_verdict_message(
        self, game: dict[str, Any], verdict: dict[str, Any]
    ) -> str:
        players_by_id = {
            player["id"]: player["name"] for player in player_entries(game)
        }
        return (
            f"{self.title} — решение режиссёра\n\n"
            f"{verdict['analysis']}\n\n"
            f"Главная роль: {players_by_id[verdict['winner_id']]}\n\n"
            f"Сцена после титров: {verdict['post_credits_scene']}"
            f"{_format_nominations(game, verdict)}"
        )


class BudgetHeistScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="budget_heist",
            title="Ограбление века за 12 франков",
            command="/game",
            lobby_intro="Бюджет исчез, цель осталась. Собирается самая дешёвая команда века.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер вымышленной комедийной игры «Ограбление века за 12 франков». "
            "Придумай абсурдно ценный объект, чрезмерно защищённое место, смешную причину "
            "исчезновения бюджета и общий набор из 3-4 дешёвых бытовых предметов. "
            "Планы должны быть явно нереалистичными и не содержать практических инструкций "
            "для настоящих преступлений. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"target":"объект","location":"место","security":"охрана",'
            '"budget_problem":"причина","available_tools":"перечень предметов",'
            '"round_one_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            keys = (
                "target",
                "location",
                "security",
                "budget_problem",
                "available_tools",
                "round_one_task",
            )
            if not _strict_fields(payload, set(keys)):
                return None
            if not all(isinstance(payload.get(key), str) for key in keys):
                return None
            return {key: compact(payload[key], 450) for key in keys}

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "Нужны строки target, location, security, budget_problem, "
                "available_tools и round_one_task."
            ),
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {
            "heist": game["content"]["opening"],
            "players": _players_payload(game),
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты гейм-мастер «Ограбления века за 12 франков». "
            "Придумай одно общее комедийное осложнение, которое бьёт по слабым местам "
            "планов, но оставляет каждому шанс спасти операцию без новых расходов. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"complication":"2 предложения","round_two_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            if not _strict_fields(payload, {"complication", "round_two_task"}):
                return None
            if not all(
                isinstance(payload.get(key), str)
                for key in ("complication", "round_two_task")
            ):
                return None
            return {
                "complication": compact(payload["complication"], 700),
                "round_two_task": compact(payload["round_two_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="Нужны строки complication и round_two_task.",
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        participants = participants_for_judging(game, 2) or participants_for_judging(
            game, 1
        )
        valid_ids = {player["id"] for player in participants}
        payload = {
            "heist": game["content"]["opening"],
            "complication": game["content"]["round_two"],
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты гейм-мастер «Ограбления века за 12 франков». "
            "Опиши последствия, выбери самый изобретательный вымышленный план, "
            "подведи смешной итог бюджета и выдай до трёх номинаций. Не объявляй ничью. "
            f"Допустимые id: {', '.join(str(value) for value in sorted(valid_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,'
            '"budget_result":"1 предложение",'
            '"nominations":[{"player_id":123,"title":"...","reason":"..."}]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            if (
                not _strict_fields(
                    payload,
                    {"analysis", "winner_id", "budget_result", "nominations"},
                )
                or not isinstance(payload.get("analysis"), str)
                or not isinstance(payload.get("budget_result"), str)
                or not _valid_int_id(winner_id, valid_ids)
            ):
                return None
            nominations = _validate_nominations(payload.get("nominations"), valid_ids)
            if nominations is None:
                return None
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "budget_result": compact(payload["budget_result"], 500),
                "nominations": nominations,
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint=("Нужны analysis, winner_id, budget_result и nominations."),
        )

    def format_opening_message(
        self, game: dict[str, Any], opening: dict[str, Any]
    ) -> str:
        return (
            f"{self.title}\n\n"
            f"Цель: {opening['target']}\n"
            f"Место: {opening['location']}\n"
            f"Охрана: {opening['security']}\n"
            f"Куда делся бюджет: {opening['budget_problem']}\n"
            f"Доступно: {opening['available_tools']}\n\n"
            f"Задание раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение своим вымышленным планом."
        )

    def format_round_two_message(
        self, game: dict[str, Any], payload: dict[str, Any]
    ) -> str:
        return (
            f"{self.title} — раунд 2\n\n"
            f"Осложнение: {payload['complication']}\n\n"
            f"Задание: {payload['round_two_task']}\n\n"
            "Ответьте reply на это сообщение спасённым планом."
        )

    def format_verdict_message(
        self, game: dict[str, Any], verdict: dict[str, Any]
    ) -> str:
        players_by_id = {
            player["id"]: player["name"] for player in player_entries(game)
        }
        return (
            f"{self.title} — итоги\n\n"
            f"{verdict['analysis']}\n\n"
            f"Лучший план: {players_by_id[verdict['winner_id']]}\n"
            f"Бюджет: {verdict['budget_result']}"
            f"{_format_nominations(game, verdict)}"
        )


class PressConferenceScenario(GameScenario):
    def __init__(self) -> None:
        super().__init__(
            game_type="press_conference",
            title="Пресс-конференция после катастрофы",
            command="/game",
            lobby_intro="Операция завершена успешно. Теперь осталось как-то объяснить последствия.",
        )

    async def generate_opening(self, game: dict[str, Any]) -> dict[str, Any] | None:
        payload = {"players": _players_payload(game)}
        prompt = (
            "Ты гейм-мастер шуточной игры «Пресс-конференция после катастрофы». "
            "Придумай последствия финальной сцены французского боевика: публичный скандал, "
            "неудачный кадр или пропавший важный предмет. Все игроки представляли одну "
            "команду и теперь должны выдать короткое официальное объяснение. "
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"scandal":"2-3 предложения","team_role":"кем была команда",'
            '"round_one_task":"один вопрос"}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            keys = ("scandal", "team_role", "round_one_task")
            if not _strict_fields(payload, set(keys)):
                return None
            if not all(isinstance(payload.get(key), str) for key in keys):
                return None
            return {
                "scandal": compact(payload["scandal"], 900),
                "team_role": compact(payload["team_role"], 350),
                "round_one_task": compact(payload["round_one_task"], 400),
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint="Нужны строки scandal, team_role и round_one_task.",
        )

    async def generate_round_two(self, game: dict[str, Any]) -> dict[str, Any] | None:
        active_ids = {str(player_id) for player_id in submitted_player_ids(game, 1)}
        payload = {
            "press_brief": game["content"]["opening"],
            "players": [
                player
                for player in _players_payload(game)
                if str(player["user_id"]) in active_ids
            ],
            "round_one_answers": _answers_payload(game, 1),
        }
        prompt = (
            "Ты ведущий «Пресс-конференции после катастрофы». "
            "Задай каждому игроку один персональный каверзный вопрос журналиста. "
            "Цепляйся за конкретное противоречие или нелепую деталь первого ответа, "
            "но не добавляй в вопрос ответ за игрока. Ключи questions — строковые player_id.\n"
            f"Допустимые player_id: {', '.join(sorted(active_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"questions":{"123":"вопрос"}}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            questions = _validate_player_id_map(
                payload, game, "questions", allowed_ids=active_ids
            )
            if questions is None:
                return None
            return {"questions": questions}

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "questions должен содержать ровно одну строку на каждого player_id."
            ),
        )

    async def generate_verdict(self, game: dict[str, Any]) -> dict[str, Any] | None:
        participants = participants_for_judging(game, 2) or participants_for_judging(
            game, 1
        )
        valid_ids = {player["id"] for player in participants}
        payload = {
            "press_brief": game["content"]["opening"],
            "questions": game["content"]["round_two"]["questions"],
            "players": [
                {"user_id": player["id"], "name": player["name"]}
                for player in participants
            ],
            "round_one_answers": _answers_payload(game, 1),
            "round_two_answers": _answers_payload(game, 2),
        }
        prompt = (
            "Ты беспристрастный ведущий «Пресс-конференции после катастрофы». "
            "Выбери лучшего официального представителя и автора худшего саморазоблачения, "
            "а затем придумай от одного до трёх коротких газетных заголовков по ответам. "
            "Не объявляй ничью; winner_id и exposed_id должны отличаться, если игроков больше одного. "
            f"Допустимые id: {', '.join(str(value) for value in sorted(valid_ids))}.\n"
            f"{untrusted_json_block(payload)}\n"
            "Верни только строгий JSON без markdown и лишних полей: "
            '{"analysis":"3-5 предложений","winner_id":123,"exposed_id":456,'
            '"headlines":["заголовок"]}.'
        )

        def validate(payload: dict[str, Any]) -> dict[str, Any] | None:
            winner_id = payload.get("winner_id")
            exposed_id = payload.get("exposed_id")
            if (
                not _strict_fields(
                    payload, {"analysis", "winner_id", "exposed_id", "headlines"}
                )
                or not isinstance(payload.get("analysis"), str)
                or not _valid_int_id(winner_id, valid_ids)
                or not _valid_int_id(exposed_id, valid_ids)
                or (len(valid_ids) > 1 and winner_id == exposed_id)
            ):
                return None
            raw_headlines = payload.get("headlines")
            if (
                not isinstance(raw_headlines, list)
                or not raw_headlines
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in raw_headlines[:3]
                )
            ):
                return None
            return {
                "analysis": compact(payload["analysis"], 1800),
                "winner_id": winner_id,
                "exposed_id": exposed_id,
                "headlines": [compact(value, 180) for value in raw_headlines[:3]],
            }

        return await request_json(
            prompt,
            validate,
            corrective_hint=(
                "Нужны analysis, разные winner_id/exposed_id и массив headlines."
            ),
        )

    def format_opening_message(
        self, game: dict[str, Any], opening: dict[str, Any]
    ) -> str:
        return (
            f"{self.title}\n\n"
            f"Скандал: {opening['scandal']}\n"
            f"Ваша роль: {opening['team_role']}\n\n"
            f"Вопрос раунда 1: {opening['round_one_task']}\n\n"
            "Ответьте reply на это сообщение официальным заявлением."
        )

    def format_round_two_message(
        self, game: dict[str, Any], payload: dict[str, Any]
    ) -> str:
        active_ids = set(active_player_ids(game))
        lines = [
            f"{player['name']}: {payload['questions'][str(player['id'])]}"
            for player in player_entries(game)
            if player["id"] in active_ids
        ]
        return (
            f"{self.title} — вопросы журналистов\n\n"
            + "\n".join(lines)
            + "\n\nОтветьте reply на это сообщение, не меняя первоначальную версию."
        )

    def format_verdict_message(
        self, game: dict[str, Any], verdict: dict[str, Any]
    ) -> str:
        players_by_id = {
            player["id"]: player["name"] for player in player_entries(game)
        }
        headlines = "\n".join(f"• {value}" for value in verdict["headlines"])
        return (
            f"{self.title} — итоги\n\n"
            f"{verdict['analysis']}\n\n"
            f"Лучший представитель: {players_by_id[verdict['winner_id']]}\n"
            f"Главное саморазоблачение: {players_by_id[verdict['exposed_id']]}\n\n"
            f"Заголовки прессы:\n{headlines}"
        )


SCENARIOS: dict[str, GameScenario] = {
    "alibi": AlibiScenario(),
    "operation": OperationScenario(),
    "pitch": PitchScenario(),
    "chase": ChaseScenario(),
    "villain_casting": VillainCastingScenario(),
    "budget_heist": BudgetHeistScenario(),
    "press_conference": PressConferenceScenario(),
}
