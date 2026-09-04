"""Versioned, deterministic rules for the personal Death Mission.

Only the repository owns the seed. This module never reads balances or clocks.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy


VERSION = "roguelite_v1"
TACTICS = {
    "balanced": ("Баланс", 6, 2),
    "stealth": ("Тихий вход", 5, 3),
    "assault": ("Штурм", 6, 1),
}
MODULES = {
    "silencer": ("Глушитель", "Тревога от действия уменьшается на 1."),
    "armor": ("Бронепластины", "Первый урон в комнате уменьшается на 1."),
    "medic": ("Полевой медик", "После обычной комнаты при состоянии 1–2: +1."),
    "scanner": ("Перехватчик", "Архив даёт ещё 1 разведданное."),
    "passport": (
        "Поддельный пропуск",
        "Один раз отменяет облаву; тревога становится 3.",
    ),
    "escape": ("Аварийный канал", "Эвакуация открывается после второго узла."),
}
ROOMS = {
    "patrol": "Патруль",
    "archive": "Архив под наблюдением",
    "shelter": "Убежище",
    "cache": "Схрон",
    "contact": "Двойной агент",
    "ambush": "Засада",
}
BOSSES = {"hq": "Штаб контрразведки", "train": "Бронепоезд"}
PHASES = ("Проникновение", "Выполнение задачи", "Отход")


def roll(seed: str, key: str, size: int = 100) -> int:
    digest = hashlib.sha256(f"{VERSION}:{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def ranked(seed: str, key: str, values) -> list[str]:
    return sorted(
        values, key=lambda value: (roll(seed, f"{key}:{value}", 2**63), value)
    )


def initial(seed: str, tactic: str) -> dict:
    _, hp, intel = TACTICS[tactic]
    route = [ranked(seed, f"room:{i}", ROOMS)[:2] for i in range(5)]
    for index in (1, 3):
        route[index] = [
            "shelter",
            next(room for room in route[index] if room != "shelter"),
        ]
    return {
        "version": VERSION,
        "phase": "room",
        "node": 0,
        "boss_phase": 0,
        "hp": hp,
        "intel": intel,
        "alarm": 0,
        "tactic": tactic,
        "modules": [],
        "route": route,
        "boss": ranked(seed, "boss", BOSSES)[0],
        "room": None,
        "offers": [],
        "checkpoint": False,
        "survived_raid": False,
        "passport_used": False,
        "armor_used": False,
        "assault_shield": tactic == "assault",
        "log": [],
        "outcome": None,
    }


def _action(id, label, *, cost=0, hp=0, intel=0, alarm=0, risk=0, damage=2):
    return dict(
        id=id,
        label=label,
        cost=cost,
        hp=hp,
        intel=intel,
        alarm=alarm,
        risk=risk,
        damage=damage,
    )


def actions(state: dict) -> list[dict]:
    phase = state["phase"]
    if phase == "room":
        return [
            dict(id=room, label=ROOMS[room]) for room in state["route"][state["node"]]
        ]
    if phase == "module":
        return [
            dict(id=mod, label=MODULES[mod][0], description=MODULES[mod][1])
            for mod in state["offers"]
        ]
    if phase not in {"action", "boss"}:
        return []
    risk = min(75, 25 + 5 * state["alarm"])
    if phase == "boss":
        cost = (
            (2, 3, 2)[state["boss_phase"]]
            if state["boss"] == "hq"
            else (1, 2, 1)[state["boss_phase"]]
        )
        choices = [
            _action(
                "plan",
                "Подготовленный проход",
                cost=cost,
                hp=-1,
                alarm=1,
                risk=35 if state["boss"] == "hq" else 30,
                damage=3,
            ),
            _action("force", "Прорываться", risk=min(90, risk + 35), alarm=1, damage=4),
        ]
    else:
        choices = {
            "patrol": [
                _action("bypass", "Обойти по разведданным", cost=1),
                _action("rush", "Проскочить патруль", risk=risk, alarm=1),
                _action("crawl", "Пройти через заграждения", hp=-1),
            ],
            "archive": [
                _action("access", "Подменить пропуск", cost=1, intel=2, alarm=1),
                _action("hack", "Вскрыть терминал", intel=2, risk=risk),
                _action("leave", "Пройти мимо"),
            ],
            "shelter": [
                _action("heal", "Восстановить группу", hp=2),
                _action("hide", "Сбить преследование", alarm=-2),
            ],
            "cache": [
                _action("intel", "Забрать разведданные", intel=1),
                _action("medical", "Забрать аптечку", hp=1),
            ],
            "contact": [
                _action("deal", "Купить сведения ценой прикрытия", hp=-1, intel=2),
                _action("leave", "Отказаться от сделки"),
            ],
            "ambush": [
                _action("divert", "Подготовить отвлекающий манёвр", cost=2),
                _action(
                    "fight", "Прорываться из засады", risk=min(85, risk + 15), alarm=1
                ),
                _action("retreat", "Отступить через опасный проход", hp=-2),
            ],
        }[state["room"]]
    for action in choices:
        action["enabled"] = state["intel"] >= action["cost"]
        action["lethal"] = action["hp"] < 0 and state["hp"] + action["hp"] <= 0
    return choices


def _damage(state: dict, amount: int) -> None:
    if "armor" in state["modules"] and not state["armor_used"]:
        amount = max(0, amount - 1)
        state["armor_used"] = True
    if state["assault_shield"]:
        amount = max(0, amount - 1)
        state["assault_shield"] = False
    state["hp"] = max(0, state["hp"] - amount)


def advance(current: dict, action_id: str, seed: str) -> dict:
    state = deepcopy(current)
    if state["outcome"]:
        raise ValueError("Забег уже завершён")
    action = next((a for a in actions(state) if a["id"] == action_id), None)
    if action is None or not action.get("enabled", True):
        raise ValueError("Действие недоступно")
    phase = state["phase"]
    if phase == "room":
        state.update(room=action_id, phase="action", armor_used=False)
        return state
    if phase == "module":
        state["modules"].append(action_id)
        state["offers"] = []
        state["phase"] = "room"
        state["checkpoint"] = state["node"] >= (
            2 if "escape" in state["modules"] else 3
        )
        return state

    failed = (
        action["risk"]
        and roll(seed, f"action:{state['node']}:{state['boss_phase']}:{action_id}")
        < action["risk"]
    )
    state["intel"] -= action["cost"]
    state["intel"] += action["intel"]
    if state["room"] == "archive" and action["intel"] and "scanner" in state["modules"]:
        state["intel"] += 1
    if action["hp"] < 0:
        _damage(state, -action["hp"])
    else:
        state["hp"] = min(6, state["hp"] + action["hp"])
    alarm = action["alarm"] + int(bool(failed))
    if alarm > 0 and "silencer" in state["modules"]:
        alarm -= 1
    state["alarm"] = max(0, state["alarm"] + alarm)
    if failed:
        _damage(state, action["damage"])
    if state["alarm"] >= 6:
        if "passport" in state["modules"] and not state["passport_used"]:
            state.update(passport_used=True, alarm=3)
        else:
            _damage(state, 2)
            state["alarm"] = 4
            if state["hp"] > 0:
                state["survived_raid"] = True
    state["intel"] = min(6, state["intel"])
    state["log"].append(
        action["label"] + (" — осложнение" if failed else " — выполнено")
    )
    if state["hp"] == 0:
        state.update(outcome="lost", phase="done")
        return state
    if phase == "boss":
        state["boss_phase"] += 1
        if state["boss_phase"] == 3:
            state.update(outcome="won", phase="done")
        return state
    if "medic" in state["modules"] and state["hp"] <= 2:
        state["hp"] += 1
    state["node"] += 1
    state["checkpoint"] = state["node"] >= (2 if "escape" in state["modules"] else 3)
    state["room"] = None
    if state["node"] == 5:
        state.update(phase="boss", armor_used=False)
    elif state["node"] in (1, 3):
        pool = [m for m in MODULES if m not in state["modules"]]
        state["offers"] = ranked(seed, f"modules:{state['node']}", pool)[:3]
        state["phase"] = "module"
    else:
        state["phase"] = "room"
    return state


def public_state(state: dict) -> dict:
    if not state:
        return {}
    result = {
        k: state[k]
        for k in (
            "phase",
            "node",
            "hp",
            "intel",
            "alarm",
            "tactic",
            "modules",
            "checkpoint",
            "log",
            "outcome",
        )
    }
    result["module_names"] = [MODULES[m][0] for m in state["modules"]]
    result["boss"] = BOSSES[state["boss"]]
    result["title"] = (
        f"{BOSSES[state['boss']]}: {PHASES[min(2, state['boss_phase'])]}"
        if state["phase"] == "boss"
        else ROOMS.get(
            state["room"],
            "Выберите модуль" if state["phase"] == "module" else "Маршрут",
        )
    )
    result["actions"] = actions(state) if not state["outcome"] else []
    return result


def validate(state: dict) -> None:
    """Reject incompatible/corrupt persisted runs before accepting any action."""
    if not isinstance(state, dict) or state.get("version") != VERSION:
        raise ValueError("unsupported mission state")
    for key, maximum in (
        ("hp", 6),
        ("intel", 6),
        ("alarm", 5),
        ("node", 5),
        ("boss_phase", 2),
    ):
        if type(state.get(key)) is not int or not 0 <= state[key] <= maximum:
            raise ValueError("invalid mission resource")
    if state.get("phase") not in {"room", "action", "module", "boss"}:
        raise ValueError("invalid mission phase")
    if state.get("boss") not in BOSSES or state.get("tactic") not in TACTICS:
        raise ValueError("unknown mission content")
    if len(state["route"]) != 5 or any(
        len(layer) != 2 or any(room not in ROOMS for room in layer)
        for layer in state["route"]
    ):
        raise ValueError("invalid mission route")
    if len(state["modules"]) > 2 or any(
        module not in MODULES for module in state["modules"]
    ):
        raise ValueError("invalid mission modules")
    public_state(state)
