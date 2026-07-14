import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import games.scenarios as scenarios_module
from games.scenarios import SCENARIOS


def test_all_planned_scenarios_are_registered():
    assert set(SCENARIOS) == {
        "alibi",
        "operation",
        "pitch",
        "chase",
        "villain_casting",
        "budget_heist",
        "press_conference",
    }
    assert all(scenario.command == "/game" for scenario in SCENARIOS.values())


def _game_snapshot():
    return {
        "players": {
            "1": {"id": 1, "name": "Анна"},
            "2": {"id": 2, "name": "Борис"},
        },
        "player_order": [1, 2],
        "active_player_ids": [1, 2],
        "moves": {
            "1": {"1": "Первый план", "2": "Первая импровизация"},
            "2": {"1": "Второй план", "2": "Вторая импровизация"},
        },
        "content": {},
    }


NEW_SCENARIO_PAYLOADS = {
    "chase": (
        {
            "mission": "Перехватить дипломата.",
            "location": "Набережная",
            "transport_rule": "Все автомобили исчезли.",
            "round_one_task": "На чём вы поедете?",
        },
        {"twist": "Начался парад.", "round_two_task": "Как продолжите?"},
        {
            "analysis": "Победа получилась эффектной.",
            "winner_id": 1,
            "nominations": [{"player_id": 2, "title": "Дрифт", "reason": "Без колёс."}],
        },
    ),
    "villain_casting": (
        {
            "movie": "Последний сыр Парижа.",
            "villain_goal": "Похитить все титры.",
            "required_prop": "Зонт",
            "round_one_task": "Представьте злодея.",
        },
        {"challenges": {"1": "Спасите монолог.", "2": "Войдите в кадр."}},
        {
            "analysis": "Режиссёр сделал выбор.",
            "winner_id": 2,
            "post_credits_scene": "Зонт получает отдельный трейлер.",
            "nominations": [],
        },
    ),
    "budget_heist": (
        {
            "target": "Золотой билет",
            "location": "Музей",
            "security": "Семь турникетов",
            "budget_problem": "Всё ушло на кофе.",
            "available_tools": "Шнурок, багет и чек",
            "round_one_task": "Каков ваш план?",
        },
        {
            "complication": "Билет больше двери.",
            "round_two_task": "Как спасёте операцию?",
        },
        {
            "analysis": "Операция почти окупилась.",
            "winner_id": 1,
            "budget_result": "Осталось два франка и чек.",
            "nominations": [],
        },
    ),
    "press_conference": (
        {
            "scandal": "Финальная погоня прошла через телестудию.",
            "team_role": "Отдел специальных поручений",
            "round_one_task": "Почему это успех?",
        },
        {"questions": {"1": "Где отчёт?", "2": "Куда делась камера?"}},
        {
            "analysis": "Пресса получила больше ответов, чем ожидала.",
            "winner_id": 1,
            "exposed_id": 2,
            "headlines": ["Отчёт найден в прямом эфире"],
        },
    ),
}


@pytest.mark.parametrize("game_type", NEW_SCENARIO_PAYLOADS)
@pytest.mark.asyncio
async def test_new_scenario_contracts(monkeypatch, game_type):
    game = _game_snapshot()
    responses = iter(NEW_SCENARIO_PAYLOADS[game_type])

    async def fake_request_json(_prompt, validator, *, corrective_hint):
        assert corrective_hint
        return validator(next(responses))

    monkeypatch.setattr(scenarios_module, "request_json", fake_request_json)
    scenario = SCENARIOS[game_type]

    opening = await scenario.generate_opening(game)
    assert opening is not None
    game["content"]["opening"] = opening

    round_two = await scenario.generate_round_two(game)
    assert round_two is not None
    game["content"]["round_two"] = round_two

    verdict = await scenario.generate_verdict(game)
    assert verdict is not None

    assert scenario.title in scenario.format_opening_message(game, opening)
    assert scenario.title in scenario.format_round_two_message(game, round_two)
    assert scenario.title in scenario.format_verdict_message(game, verdict)


@pytest.mark.parametrize("game_type", NEW_SCENARIO_PAYLOADS)
@pytest.mark.asyncio
async def test_new_scenario_rejects_invalid_verdict_ids(monkeypatch, game_type):
    game = _game_snapshot()
    opening, round_two, valid_verdict = NEW_SCENARIO_PAYLOADS[game_type]
    game["content"] = {"opening": opening, "round_two": round_two}
    invalid_verdict = dict(valid_verdict)
    if game_type == "press_conference":
        invalid_verdict["exposed_id"] = invalid_verdict["winner_id"]
    else:
        invalid_verdict["winner_id"] = 999

    async def fake_request_json(_prompt, validator, *, corrective_hint):
        assert corrective_hint
        assert validator(invalid_verdict) is None
        return None

    monkeypatch.setattr(scenarios_module, "request_json", fake_request_json)

    assert await SCENARIOS[game_type].generate_verdict(game) is None
