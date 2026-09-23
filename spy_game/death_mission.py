"""Versioned, deterministic rules for the personal Death Mission.

Only the repository owns the seed. This module never reads balances or clocks.
Content lives in ``death_mission_content``; every function looks up the ruleset
pinned in ``state["version"]`` so open runs keep their original numbers.

``resolve`` is the single place where an action changes a state. ``advance``
rolls the complication and calls it; ``preview`` calls it for both branches;
``finale_odds`` folds it into a dynamic program over the finale.
"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from functools import lru_cache
from . import death_mission_challenges as challenges

from .death_mission_content import (
    DEFAULT_VERSION,
    RULESETS,
    Action,
    Ruleset,
    rules,
)

__all__ = [
    "DEFAULT_VERSION",
    "RULESETS",
    "VERSION",
    "TACTIC_NAMES",
    "roll",
    "initial",
    "actions",
    "advance",
    "resolve",
    "preview",
    "finale_odds",
    "public_state",
    "describe_event",
    "validate",
    "rules",
]

VERSION = "roguelite_v1"
TACTIC_NAMES = {
    tactic_id: tactic.name for ruleset in RULESETS.values() for tactic_id, tactic in ruleset.tactics.items()
}
TACTICS = TACTIC_NAMES  # id -> name; the set of start tactics across versions
SPECIALISTS = {
    "saboteur": dict(name="Саботажник", emoji="💣", reduction=10, ability="Подорвать проход"),
    "ghost_agent": dict(name="Агент-призрак", emoji="👻", reduction=15, ability="Стереть след"),
}


def roll(seed: str, key: str, size: int = 100, version: str = VERSION) -> int:
    digest = hashlib.sha256(f"{version}:{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % size


def ranked(seed: str, key: str, values, version: str = VERSION) -> list[str]:
    return sorted(
        values,
        key=lambda value: (roll(seed, f"{key}:{value}", 2**63, version), value),
    )


def _rules(state: dict) -> Ruleset:
    return rules(state["version"])


def initial(seed: str, tactic: str, version: str = DEFAULT_VERSION, *, specialists=()) -> dict:
    ruleset = rules(version)
    start = ruleset.tactics[tactic]
    route = [ranked(seed, f"room:{i}", ruleset.node_rooms[i] or ruleset.rooms, version)[:2] for i in range(5)]
    for index in ruleset.shelter_nodes:
        route[index] = [
            "shelter",
            next(room for room in route[index] if room != "shelter"),
        ]
    state = {
        "version": version,
        "phase": "room",
        "node": 0,
        "boss_phase": 0,
        "hp": start.hp,
        "intel": start.intel,
        "alarm": 0,
        "tactic": tactic,
        "modules": [],
        "route": route,
        "boss": ranked(seed, "boss", ruleset.bosses, version)[0],
        "room": None,
        "offers": [],
        "checkpoint": False,
        "survived_raid": False,
        "passport_used": False,
        "armor_used": False,
        "assault_shield": start.shield,
        "log": [],
        "outcome": None,
    }
    if ruleset.specialists:
        state["support"] = {key: True for key in SPECIALISTS if key in specialists}
    if ruleset.archive_challenge:
        state["challenges"] = [challenges.board(seed, i) for i in range(5)]
        state["challenge"] = None
    return state


def module_pool(version, node, modules):
    ruleset = rules(version)
    return [
        key
        for key in ruleset.modules
        if key not in modules
        and not (ruleset.filter_spent_escape and key == "escape" and node >= ruleset.checkpoint_node)
    ]


def _specialist(state, action_id):
    if not _rules(state).specialists:
        return None
    if (state["phase"] == "boss" and state["boss_phase"] < 2 and action_id == "force") or (
        state["phase"] == "action" and state["room"] == "ambush" and action_id == "fight"
    ):
        return "saboteur"
    if state["phase"] == "boss" and state["boss_phase"] == 2 and action_id in {"plan", "sewer"}:
        return "ghost_agent"
    return None


def _risk(state: dict, template: Action) -> int:
    ruleset = _rules(state)
    modifier = ruleset.tactics[state["tactic"]].risk_modifier
    floor = 0
    if state["phase"] != "boss" and ruleset.node_risk_floor[state["node"]]:
        floor = max(0, ruleset.node_risk_floor[state["node"]] + modifier)
    if template.risk_kind == "none":
        return floor
    if template.risk_kind == "fixed":
        return max(floor, template.risk)
    base = ruleset.base_risk + ruleset.risk_per_alarm * state["alarm"] + modifier
    if state["phase"] != "boss":
        base += ruleset.node_risk[state["node"]]
    base = max(0, min(ruleset.risk_cap, base))
    return max(floor, min(template.risk_cap, base + template.risk))


def _templates(state: dict) -> tuple[Action, ...]:
    ruleset = _rules(state)
    if state["phase"] == "boss":
        return ruleset.boss_actions[state["boss"]][state["boss_phase"]]
    return ruleset.room_actions[state["room"]]


def _checkpoint(state: dict) -> bool:
    ruleset = _rules(state)
    node = ruleset.escape_checkpoint_node if "escape" in state["modules"] else ruleset.checkpoint_node
    return state["node"] >= node


def actions(state: dict) -> list[dict]:
    """Public buttons for the current phase, without previews or odds."""
    ruleset = _rules(state)
    phase = state["phase"]
    if phase == "challenge":
        challenge = state["challenge"]
        return [
            dict(id=f"cell_{cell}", label=f"Клетка {cell // 4 + 1}:{cell % 4 + 1}")
            for cell in challenges.moves(challenge)
        ] + [dict(id="skip_puzzle", label="Пропустить схему без штрафа")]
    if phase == "room":
        return [
            dict(
                id=room,
                label=ruleset.rooms[room],
                options=[a.label for a in ruleset.room_actions[room]],
            )
            for room in state["route"][state["node"]]
        ]
    if phase == "module":
        return [
            dict(
                id=mod,
                label=ruleset.modules[mod].name,
                description=ruleset.modules[mod].description,
            )
            for mod in state["offers"]
        ]
    if phase not in {"action", "boss"}:
        return []
    result = []
    for template in _templates(state):
        damage, cost = template.damage, template.cost
        if phase != "boss":
            damage = ruleset.node_damage[state["node"]]
            if cost:
                # Deeper nodes: paying your way through costs more intel.
                cost += ruleset.node_cost[state["node"]]
        result.append(
            dict(
                id=template.id,
                label=template.label,
                cost=cost,
                hp=template.hp,
                intel=template.intel,
                alarm=template.alarm,
                risk=_risk(state, template),
                damage=damage,
                enabled=state["intel"] >= cost,
            )
        )
    for action in list(result):
        specialist = _specialist(state, action["id"])
        if specialist and state.get("support", {}).get(specialist):
            info = SPECIALISTS[specialist]
            result.append(
                dict(
                    action,
                    id=f"{action['id']}:{specialist}",
                    base_id=action["id"],
                    specialist=specialist,
                    label=f"{info['emoji']} {info['ability']}: {action['label']}",
                    base_risk=action["risk"],
                    risk=max(0, action["risk"] - info["reduction"]),
                )
            )
    return result


def _damage(state: dict, amount: int, event: dict) -> None:
    if "armor" in state["modules"] and not state["armor_used"]:
        absorbed = min(1, amount)
        amount -= absorbed
        event["absorbed"] += absorbed
        state["armor_used"] = True
    if state["assault_shield"] and (amount > 0 or not _rules(state).preserve_unused_shield):
        absorbed = min(1, amount)
        amount -= absorbed
        event["absorbed"] += absorbed
        state["assault_shield"] = False
    state["hp"] = max(0, state["hp"] - amount)


def _title(state: dict) -> str:
    ruleset = _rules(state)
    if state["phase"] == "challenge":
        return "Архив: восстановите цепь"
    if state["phase"] == "boss":
        return f"{ruleset.bosses[state['boss']]}: {ruleset.phases[min(2, state['boss_phase'])]}"
    if state["room"]:
        return ruleset.rooms[state["room"]]
    return "Выберите модуль" if state["phase"] == "module" else "Маршрут"


def resolve(current: dict, action_id: str, failed: bool) -> tuple[dict, dict | None]:
    """Apply one action with a known complication outcome.

    Returns the new state and a structured event (``None`` for room and module
    picks, which change nothing measurable). Never rolls dice.
    """
    state = deepcopy(current)
    if state["outcome"]:
        raise ValueError("Забег уже завершён")
    action = next((a for a in actions(state) if a["id"] == action_id), None)
    if action is None or not action.get("enabled", True):
        raise ValueError("Действие недоступно")
    ruleset = _rules(state)
    phase = state["phase"]
    if phase == "room":
        state.update(room=action_id, phase="action", armor_used=False)
        if ruleset.archive_challenge and action_id == "archive":
            state.update(phase="challenge", challenge=deepcopy(state["challenges"][state["node"]]))
        return state, None
    if phase == "challenge":
        outcome = "skipped"
        if action_id != "skip_puzzle":
            state["challenge"], outcome = challenges.advance(state["challenge"], int(action_id[5:]))
        if outcome is None:
            return state, None
        before = state["intel"]
        if outcome == "solved":
            state["intel"] = min(ruleset.max_intel, state["intel"] + 1)
        state.update(phase="action", challenge=None)
        event = dict(
            kind="challenge",
            node=state["node"],
            title="Схема архива",
            hp=0,
            intel=state["intel"] - before,
            alarm=0,
            failed=False,
            label={"solved": "Цепь восстановлена", "missed": "Ходы схемы закончились", "skipped": "Схема пропущена"}[
                outcome
            ],
        )
        state["log"].append(event)
        return state, event
    if phase == "module":
        state["modules"].append(action_id)
        state["offers"] = []
        state["phase"] = "room"
        state["checkpoint"] = _checkpoint(state)
        return state, None

    failed = bool(failed and action["risk"])
    specialist = action.get("specialist")
    if specialist:
        state["support"][specialist] = False
    event = dict(
        kind="action",
        node=state["node"],
        boss_phase=state["boss_phase"] if phase == "boss" else None,
        title=_title(state),
        action=action_id,
        label=action["label"],
        risk=action["risk"],
        failed=failed,
        hp=0,
        intel=0,
        alarm=0,
        raid=False,
        passport=False,
        absorbed=0,
        medic=0,
    )
    if specialist:
        event["specialist"] = specialist
    before = dict(hp=state["hp"], intel=state["intel"], alarm=state["alarm"])
    state["intel"] -= action["cost"]
    state["intel"] += action["intel"]
    if state["room"] == "archive" and action["intel"] and "scanner" in state["modules"]:
        state["intel"] += 1
    if action["hp"] < 0:
        _damage(state, -action["hp"], event)
    else:
        state["hp"] = min(ruleset.max_hp, state["hp"] + action["hp"])
    alarm = action["alarm"] + int(failed)
    if "silencer" in state["modules"]:
        if ruleset.silencer_only_complications:
            alarm -= int(failed)
        elif alarm > 0:
            alarm -= 1
    state["alarm"] = max(0, state["alarm"] + alarm)
    if failed:
        _damage(state, action["damage"], event)
    if state["alarm"] >= ruleset.raid_alarm:
        if "passport" in state["modules"] and not state["passport_used"]:
            state.update(passport_used=True, alarm=ruleset.passport_alarm)
            event["passport"] = True
        else:
            raid_damage = ruleset.raid_damage
            if "passport" in state["modules"]:
                raid_damage = max(0, raid_damage - ruleset.passport_raid_reduction)
            _damage(state, raid_damage, event)
            state["alarm"] = ruleset.raid_reset
            event["raid"] = True
            if state["hp"] > 0:
                state["survived_raid"] = True
    state["intel"] = min(ruleset.max_intel, state["intel"])
    if state["hp"] == 0:
        state.update(outcome="lost", phase="done")
    elif phase == "boss":
        state["boss_phase"] += 1
        if state["boss_phase"] == 3:
            state.update(outcome="won", phase="done")
    if (
        state["outcome"] is None
        and "medic" in state["modules"]
        and state["hp"] <= ruleset.medic_threshold
        and (phase != "boss" or ruleset.medic_in_boss)
    ):
        state["hp"] += 1
        event["medic"] = 1
    if state["outcome"] is None and phase != "boss":
        state["node"] += 1
        state["checkpoint"] = _checkpoint(state)
        state["room"] = None
        if state["node"] == 5:
            state.update(phase="boss", armor_used=False)
        elif state["node"] in ruleset.module_nodes:
            state["offers"] = []  # filled by advance(); previews leave it empty
            state["phase"] = "module"
        else:
            state["phase"] = "room"
    for key in ("hp", "intel", "alarm"):
        event[key] = state[key] - before[key]
    state["log"].append(event)
    return state, event


def advance(current: dict, action_id: str, seed: str) -> tuple[dict, list[dict]]:
    """Roll the complication for this exact node/phase/action and apply it.

    Returns the new state and the structured events produced by this step
    (empty when the player only picks a room or a module).
    """
    action = next((a for a in actions(current) if a["id"] == action_id), None)
    if current["outcome"]:
        raise ValueError("Забег уже завершён")
    if action is None or not action.get("enabled", True):
        raise ValueError("Действие недоступно")
    risk = action.get("risk", 0)
    rolled = (
        roll(
            seed,
            f"action:{current['node']}:{current['boss_phase']}:{action.get('base_id', action_id)}",
            version=current["version"],
        )
        if risk
        else None
    )
    failed = rolled is not None and rolled < risk
    state, event = resolve(current, action_id, failed)
    if event is not None and rolled is not None:
        # Presentation only: 1..risk is a complication, risk+1..100 succeeds.
        # Keep the original draw/key and every gameplay branch unchanged.
        event["roll"] = rolled + 1
    if state["phase"] == "module" and not state["offers"]:
        ruleset = _rules(state)
        pool = module_pool(state["version"], state["node"], state["modules"])
        state["offers"] = ranked(seed, f"modules:{state['node']}", pool, state["version"])[: ruleset.module_offers]
    return state, ([] if event is None else [event])


def _branch(state: dict, event: dict | None) -> dict:
    return dict(
        hp=state["hp"],
        intel=state["intel"],
        alarm=state["alarm"],
        dead=state["outcome"] == "lost",
        raid=bool(event and event["raid"]),
        passport=bool(event and event["passport"]),
        absorbed=event["absorbed"] if event else 0,
        medic=event["medic"] if event else 0,
    )


def preview(state: dict, action_id: str) -> dict:
    """Both branches of an action: ``success`` always, ``failure`` when it rolls."""
    action = next(a for a in actions(state) if a["id"] == action_id)
    success_state, success_event = resolve(state, action_id, False)
    result = dict(success=_branch(success_state, success_event), failure=None)
    if action.get("risk"):
        failure_state, failure_event = resolve(state, action_id, True)
        result["failure"] = _branch(failure_state, failure_event)
    return result


@lru_cache(maxsize=200_000)
def _boss_odds(
    version: str,
    boss: str,
    modules: tuple[str, ...],
    tactic: str,
    boss_phase: int,
    hp: int,
    intel: int,
    alarm: int,
    armor_used: bool,
    assault_shield: bool,
    passport_used: bool,
    support: tuple[str, ...] = (),
) -> float:
    if boss_phase >= 3:
        return 1.0
    state = dict(
        version=version,
        phase="boss",
        node=5,
        boss_phase=boss_phase,
        hp=hp,
        intel=intel,
        alarm=alarm,
        tactic=tactic,
        modules=list(modules),
        route=[],
        boss=boss,
        room=None,
        offers=[],
        checkpoint=True,
        survived_raid=False,
        passport_used=passport_used,
        armor_used=armor_used,
        assault_shield=assault_shield,
        log=[],
        outcome=None,
        support={key: True for key in support},
    )
    best = 0.0
    for action in actions(state):
        if not action["enabled"]:
            continue
        total = 0.0
        risk = action["risk"] / 100
        for failed, weight in ((False, 1 - risk), (True, risk)):
            if weight <= 0:
                continue
            after, _ = resolve(state, action["id"], failed)
            total += weight * (
                0.0
                if after["outcome"] == "lost"
                else _boss_odds(
                    version,
                    boss,
                    modules,
                    tactic,
                    after["boss_phase"],
                    after["hp"],
                    after["intel"],
                    after["alarm"],
                    after["armor_used"],
                    after["assault_shield"],
                    after["passport_used"],
                    tuple(key for key, ready in after.get("support", {}).items() if ready),
                )
            )
        best = max(best, total)
    return best


def finale_odds(state: dict, modules: list[str] | None = None) -> float:
    """Probability (0..1) of surviving the finale with optimal play.

    Exact during the finale. Before it, this is the chance if the finale
    started right now with the current resources: a hypothetical that uses
    only public information and lets the player compare options.
    """
    if state["outcome"] == "won":
        return 1.0
    if state["outcome"]:
        return 0.0
    in_boss = state["phase"] == "boss"
    return _boss_odds(
        state["version"],
        state["boss"],
        tuple(state["modules"] if modules is None else modules),
        state["tactic"],
        state["boss_phase"] if in_boss else 0,
        state["hp"],
        state["intel"],
        state["alarm"],
        state["armor_used"] if in_boss else False,
        state["assault_shield"],
        state["passport_used"],
        tuple(key for key, ready in state.get("support", {}).items() if ready),
    )


def _percent(value: float) -> int:
    return int(round(100 * value))


def _action_odds(state: dict, action: dict) -> int:
    if state["phase"] == "module":
        return _percent(finale_odds(state, state["modules"] + [action["id"]]))
    if state["phase"] in {"room", "challenge"}:
        return _percent(finale_odds(state))
    total = 0.0
    risk = action["risk"] / 100
    for failed, weight in ((False, 1 - risk), (True, risk)):
        if weight <= 0:
            continue
        after, _ = resolve(state, action["id"], failed)
        total += weight * finale_odds(after)
    return _percent(total)


def signed(value: int) -> str:
    return f"+{value}" if value > 0 else f"−{-value}"


def describe_event(event) -> str:
    """One Telegram-safe line for a structured or legacy string log entry."""
    if isinstance(event, str):
        return event
    parts = []
    for key, glyph in (("hp", "❤️"), ("intel", "🧠"), ("alarm", "🚨")):
        if event.get(key):
            parts.append(f"{glyph} {signed(event[key])}")
    if event.get("absorbed"):
        parts.append(f"защита поглотила {event['absorbed']}")
    if event.get("medic"):
        parts.append("медик +1")
    if event.get("passport"):
        parts.append("пропуск отменил облаву")
    if event.get("raid"):
        parts.append("облава")
    status = "осложнение" if event.get("failed") else "выполнено"
    detail = ", ".join(parts) or "без изменений"
    if event.get("roll") is not None:
        detail = f"бросок {event['roll']}/100, риск {event['risk']}%; " + detail
    return f"{event['label']} — {status}: {detail}"


def public_state(state: dict, *, forecasts: bool = True) -> dict:
    if not state:
        return {}
    ruleset = _rules(state)
    result = {
        k: state[k]
        for k in (
            "version",
            "phase",
            "node",
            "boss_phase",
            "hp",
            "intel",
            "alarm",
            "tactic",
            "modules",
            "checkpoint",
            "outcome",
            "armor_used",
            "assault_shield",
            "passport_used",
        )
    }
    result["support"] = dict(state.get("support", {}))
    if state["phase"] == "challenge":
        result["challenge"] = deepcopy(state["challenge"])
    if not forecasts:
        # Public observation for offline policies; omit presentation work and
        # forecasts, but expose no extra hidden information.
        result.update(
            max_hp=ruleset.max_hp,
            max_intel=ruleset.max_intel,
            raid_alarm=ruleset.raid_alarm,
            boss_id=state["boss"],
            room_id=state["room"],
            actions=actions(state) if not state["outcome"] else [],
            odds=None,
        )
        return result
    result.update(
        max_hp=ruleset.max_hp,
        max_intel=ruleset.max_intel,
        raid_alarm=ruleset.raid_alarm,
        checkpoint_node=(ruleset.escape_checkpoint_node if "escape" in state["modules"] else ruleset.checkpoint_node),
        module_nodes=list(ruleset.module_nodes),
        rules_summary=list(ruleset.summary),
        module_names=[ruleset.modules[m].name for m in state["modules"]],
        boss=ruleset.bosses[state["boss"]],
        boss_id=state["boss"],
        room_id=state["room"],
        phase_names=list(ruleset.phases),
        title=_title(state),
        events=[e if isinstance(e, dict) else dict(kind="text", label=e) for e in state["log"]],
        log=[describe_event(e) for e in state["log"]],
        map=public_map(state),
        specialists=[dict(id=key, ready=ready, **SPECIALISTS[key]) for key, ready in state.get("support", {}).items()],
        raid_hint=raid_hint(state),
    )
    if state["outcome"]:
        result.update(actions=[], odds=None)
        return result
    result["odds"] = _percent(finale_odds(state))
    result["actions"] = []
    for action in actions(state):
        entry = dict(action)
        if "cost" in action:
            # Show incremental complication alarm after the silencer, not an
            # unconditional +1 that contradicts the actual preview.
            if "silencer" not in state["modules"]:
                entry["complication_alarm"] = 1
            elif ruleset.silencer_only_complications:
                entry["complication_alarm"] = 0
            else:
                entry["complication_alarm"] = max(0, action["alarm"]) - max(0, action["alarm"] - 1)
            branches = preview(state, action["id"]) if action["enabled"] else None
            entry["preview"] = branches
            deaths = [b["dead"] for b in (branches or {}).values() if b is not None]
            entry["certain_death"] = bool(deaths) and all(deaths)
            entry["may_die"] = any(deaths)
            entry["lethal"] = entry["certain_death"]
        entry["odds"] = _action_odds(state, action) if action.get("enabled", True) else None
        result["actions"].append(entry)
    return result


def raid_hint(state):
    ruleset = _rules(state)
    if "passport" in state["modules"] and not state["passport_used"]:
        return f"При тревоге {ruleset.raid_alarm} пропуск отменит облаву; тревога станет {ruleset.passport_alarm}."
    damage = ruleset.raid_damage - (ruleset.passport_raid_reduction if "passport" in state["modules"] else 0)
    return f"При тревоге {ruleset.raid_alarm}: облава, базовый урон {max(0, damage)}, тревога {ruleset.raid_reset}. Доступная защита поглощает урон."


def public_map(state):
    """Only visited and current rooms; no future offers or private challenge boards."""
    ruleset = _rules(state)
    visited = {
        e["node"]: e["title"]
        for e in state["log"]
        if isinstance(e, dict) and e.get("kind") == "action" and e.get("boss_phase") is None
    }
    result = []
    for node in range(5):
        if node < state["node"]:
            result.append(dict(node=node, status="visited", rooms=[dict(label=visited.get(node, "Узел пройден"))]))
        elif node == state["node"]:
            offered = state["route"][node] if state["phase"] == "room" else ([state["room"]] if state["room"] else [])
            result.append(
                dict(node=node, status="current", rooms=[dict(id=key, label=ruleset.rooms[key]) for key in offered])
            )
        else:
            result.append(dict(node=node, status="hidden", rooms=[]))
    return result


def validate(state: dict) -> None:
    """Reject incompatible/corrupt persisted runs before accepting any action."""
    if not isinstance(state, dict) or state.get("version") not in RULESETS:
        raise ValueError("unsupported mission state")
    ruleset = _rules(state)
    for key, maximum in (
        ("hp", ruleset.max_hp),
        ("intel", ruleset.max_intel),
        ("alarm", ruleset.raid_alarm - 1),
        ("node", 5),
        ("boss_phase", 2),
    ):
        if type(state.get(key)) is not int or not 0 <= state[key] <= maximum:
            raise ValueError("invalid mission resource")
    phases = {"room", "action", "module", "boss"} | ({"challenge"} if ruleset.archive_challenge else set())
    if state.get("phase") not in phases:
        raise ValueError("invalid mission phase")
    if state.get("boss") not in ruleset.bosses or state.get("tactic") not in ruleset.tactics:
        raise ValueError("unknown mission content")
    if len(state["route"]) != 5 or any(
        len(layer) != 2 or any(room not in ruleset.rooms for room in layer) for layer in state["route"]
    ):
        raise ValueError("invalid mission route")
    if len(state["modules"]) > 2 or any(module not in ruleset.modules for module in state["modules"]):
        raise ValueError("invalid mission modules")
    if not isinstance(state.get("log"), list) or any(not isinstance(e, (str, dict)) for e in state["log"]):
        raise ValueError("invalid mission log")
    if ruleset.specialists:
        support = state.get("support")
        if not isinstance(support, dict) or any(
            key not in SPECIALISTS or type(value) is not bool for key, value in support.items()
        ):
            raise ValueError("invalid mission specialists")
    if ruleset.archive_challenge:
        boards = state.get("challenges")
        if not isinstance(boards, list) or len(boards) != 5:
            raise ValueError("invalid archive challenges")
        for challenge in [*boards, *([state.get("challenge")] if state["phase"] == "challenge" else [])]:
            if (
                not isinstance(challenge, dict)
                or challenge.get("size") != 4
                or challenge.get("goal") != 15
                or challenge.get("start") != 0
            ):
                raise ValueError("invalid archive board")
            if (
                not isinstance(challenge.get("blocked"), list)
                or not isinstance(challenge.get("path"), list)
                or not challenge["path"]
            ):
                raise ValueError("invalid archive path")
            if type(challenge.get("moves_left")) is not int or not 0 <= challenge["moves_left"] <= 8:
                raise ValueError("invalid archive moves")
            if any(type(cell) is not int or not 0 <= cell < 16 for cell in challenge["blocked"] + challenge["path"]):
                raise ValueError("invalid archive cell")
    public_state(state)
