"""Versioned content tables for the personal Death Mission.

Every number a run depends on lives in one immutable ``Ruleset``. The engine in
``death_mission.py`` only interprets these tables; an open run keeps using the
ruleset it was started with, so old versions must never be edited in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Tactic:
    name: str
    hp: int
    intel: int
    risk_modifier: int = 0
    shield: bool = False
    unlock: str = ""


@dataclass(frozen=True)
class Module:
    name: str
    description: str


@dataclass(frozen=True)
class Action:
    """One button. ``risk_kind`` is ``none``, ``base`` (alarm formula) or ``fixed``."""

    id: str
    label: str
    cost: int = 0
    hp: int = 0
    intel: int = 0
    alarm: int = 0
    risk_kind: str = "none"
    risk: int = 0
    risk_cap: int = 100
    damage: int = 2


@dataclass(frozen=True)
class Ruleset:
    version: str
    tactics: dict[str, Tactic]
    modules: dict[str, Module]
    rooms: dict[str, str]
    room_actions: dict[str, tuple[Action, ...]]
    bosses: dict[str, str]
    phases: tuple[str, str, str]
    boss_actions: dict[str, tuple[tuple[Action, ...], ...]]
    max_hp: int = 6
    max_intel: int = 6
    raid_alarm: int = 6
    raid_damage: int = 2
    raid_reset: int = 4
    passport_alarm: int = 3
    passport_raid_reduction: int = 0
    base_risk: int = 25
    risk_per_alarm: int = 5
    risk_cap: int = 75
    node_risk: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    node_risk_floor: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    node_damage: tuple[int, int, int, int, int] = (2, 2, 2, 2, 2)
    node_cost: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
    # Room pool per node; an empty tuple means every room may appear.
    node_rooms: tuple[tuple[str, ...], ...] = ((), (), (), (), ())
    medic_threshold: int = 2
    silencer_only_complications: bool = False
    medic_in_boss: bool = False
    checkpoint_node: int = 3
    escape_checkpoint_node: int = 2
    module_nodes: tuple[int, ...] = (1, 3)
    module_offers: int = 3
    shelter_nodes: tuple[int, ...] = (1, 3)
    bonus_min_agents: int = 0
    tier3_bonus: int = 2
    tier4_bonus: int = 1
    summary: tuple[str, ...] = field(default_factory=tuple)


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
MODULES_V1 = {
    "silencer": Module("Глушитель", "Тревога от действия уменьшается на 1."),
    "armor": Module("Бронепластины", "Первый урон в комнате уменьшается на 1."),
    "medic": Module("Полевой медик", "После обычной комнаты при состоянии 1–2: +1."),
    "scanner": Module("Перехватчик", "Архив даёт ещё 1 разведданное."),
    "passport": Module("Поддельный пропуск", "Один раз отменяет облаву; тревога становится 3."),
    "escape": Module("Аварийный канал", "Эвакуация открывается после второго узла."),
}
ROOM_ACTIONS_V1 = {
    "patrol": (
        Action("bypass", "Обойти по разведданным", cost=1),
        Action("rush", "Проскочить патруль", alarm=1, risk_kind="base"),
        Action("crawl", "Пройти через заграждения", hp=-1),
    ),
    "archive": (
        Action("access", "Подменить пропуск", cost=1, intel=2, alarm=1),
        Action("hack", "Вскрыть терминал", intel=2, risk_kind="base"),
        Action("leave", "Пройти мимо"),
    ),
    "shelter": (
        Action("heal", "Восстановить группу", hp=2),
        Action("hide", "Сбить преследование", alarm=-2),
    ),
    "cache": (
        Action("intel", "Забрать разведданные", intel=1),
        Action("medical", "Забрать аптечку", hp=1),
    ),
    "contact": (
        Action("deal", "Купить сведения ценой прикрытия", hp=-1, intel=2),
        Action("leave", "Отказаться от сделки"),
    ),
    "ambush": (
        Action("divert", "Подготовить отвлекающий манёвр", cost=2),
        Action(
            "fight",
            "Прорываться из засады",
            alarm=1,
            risk_kind="base",
            risk=15,
            risk_cap=85,
        ),
        Action("retreat", "Отступить через опасный проход", hp=-2),
    ),
}


def _boss_v1(boss: str) -> tuple[tuple[Action, ...], ...]:
    costs = (2, 3, 2) if boss == "hq" else (1, 2, 1)
    plan_risk = 35 if boss == "hq" else 30
    return tuple(
        (
            Action(
                "plan",
                "Подготовленный проход",
                cost=cost,
                hp=-1,
                alarm=1,
                risk_kind="fixed",
                risk=plan_risk,
                damage=3,
            ),
            Action(
                "force",
                "Прорываться",
                alarm=1,
                risk_kind="base",
                risk=35,
                risk_cap=90,
                damage=4,
            ),
        )
        for cost in costs
    )


ROGUELITE_V1 = Ruleset(
    version="roguelite_v1",
    tactics={
        "balanced": Tactic("Баланс", 6, 2),
        "stealth": Tactic("Тихий вход", 5, 3, unlock="Пройти узел 3 в трёх забегах"),
        "assault": Tactic(
            "Штурм",
            6,
            1,
            shield=True,
            unlock="Пережить облаву и пройти узел 3 в одном забеге",
        ),
    },
    modules=MODULES_V1,
    rooms=ROOMS,
    room_actions=ROOM_ACTIONS_V1,
    bosses=BOSSES,
    phases=PHASES,
    boss_actions={boss: _boss_v1(boss) for boss in BOSSES},
    summary=(
        "Осложнение в комнате: урон 2 и тревога +1.",
        "Тревога 6: облава, урон 2 и тревога 4.",
    ),
)


# --- roguelite_v2 -----------------------------------------------------------
# Shipped in 152ee01. Preserve these numbers for open runs. The initial
# finale-greedy simulator did not establish a bound on whole-route returns.

DANGER_ROOMS = ("patrol", "archive", "contact", "ambush")
# Under surveillance nothing is free: walking away is noticed.
ROOM_ACTIONS_V2 = {
    **ROOM_ACTIONS_V1,
    "archive": (
        Action("access", "Подменить пропуск", cost=1, intel=2, alarm=1),
        Action("hack", "Вскрыть терминал", intel=2, risk_kind="base"),
        Action("leave", "Уйти, пока не заметили", alarm=1),
    ),
    "contact": (
        Action("deal", "Купить сведения ценой прикрытия", hp=-1, intel=2),
        Action("leave", "Отказаться: агент донесёт", alarm=1),
    ),
}
MODULES_V2 = {
    **MODULES_V1,
    "silencer": Module("Глушитель", "Осложнение не поднимает тревогу."),
    "medic": Module("Полевой медик", "После комнаты или фазы финала при состоянии 1–2: +1."),
    "passport": Module(
        "Поддельный пропуск",
        "Один раз отменяет облаву (тревога станет 3); следующие облавы бьют на 1 слабее.",
    ),
}


def _boss_v2(boss: str) -> tuple[tuple[Action, ...], ...]:
    """Three different phases; every roll scales with alarm.

    Complication damage grows 3 -> 4 -> 5, so each point of state at the entrance
    changes how many complications the group survives.
    """
    hq = boss == "hq"
    costs = (2, 3, 2) if hq else (1, 2, 1)
    plan_risk = (10, 15, 20) if hq else (5, 10, 15)
    damage = (3, 4, 5)
    # Loud and cheap: the highest risk, and every use adds 2 alarm.
    force = Action(
        "force",
        "Прорываться",
        alarm=2,
        risk_kind="base",
        risk=35,
        risk_cap=85,
        damage=4,
    )
    # The middle option of each phase trades something other than intel.
    extras = (
        Action(
            "recon",
            "Разведать подходы",
            intel=1,
            alarm=1,
            risk_kind="base",
            risk=30,
            damage=3,
        ),
        Action(
            "bribe",
            "Подкупить охрану",
            cost=1,
            alarm=1,
            risk_kind="base",
            risk=25,
            damage=4,
        ),
        Action(
            "sewer",
            "Уйти через коллектор",
            hp=-1,
            alarm=1,
            risk_kind="base",
            risk=15,
            damage=5,
        ),
    )
    return tuple(
        (
            # Quiet and expensive: lowest risk, no alarm.
            Action(
                "plan",
                "Подготовленный проход",
                cost=cost,
                risk_kind="base",
                risk=risk,
                risk_cap=70,
                damage=dmg,
            ),
            replace(force, damage=max(force.damage, dmg)),
            extra,
        )
        for cost, risk, dmg, extra in zip(costs, plan_risk, damage, extras)
    )


ROGUELITE_V2 = Ruleset(
    version="roguelite_v2",
    tactics={
        "balanced": Tactic("Баланс", 6, 2),
        "stealth": Tactic(
            "Тихий вход",
            5,
            2,
            risk_modifier=-3,
            unlock="Пройти узел 3 в трёх забегах",
        ),
        "assault": Tactic(
            "Штурм",
            6,
            0,
            shield=True,
            unlock="Пережить облаву и пройти узел 3 в одном забеге",
        ),
    },
    modules=MODULES_V2,
    rooms=ROOMS,
    room_actions=ROOM_ACTIONS_V2,
    bosses=BOSSES,
    phases=PHASES,
    boss_actions={boss: _boss_v2(boss) for boss in BOSSES},
    node_rooms=((), (), (), DANGER_ROOMS, DANGER_ROOMS),
    passport_raid_reduction=1,
    node_risk=(0, 0, 5, 10, 15),
    node_damage=(2, 2, 2, 3, 3),
    node_cost=(0, 0, 0, 1, 1),
    shelter_nodes=(1,),
    medic_in_boss=True,
    silencer_only_complications=True,
    summary=(
        "Осложнение в комнате: урон 2 (узлы 4–5: 3) и тревога +1.",
        "Узлы 3–5: риск +5/+10/+15 п.п., плата разведданными на узлах 4–5 дороже на 1.",
        "Тревога добавляет 5 п.п. риска к каждому броску, включая финал.",
        "Тревога 6: облава, урон 2 и тревога 4.",
    ),
)

# New rules only apply to newly opened runs; v1/v2 remain replay-compatible.
ROGUELITE_V3 = replace(
    ROGUELITE_V2,
    version="roguelite_v3",
    bonus_min_agents=5,
    tier3_bonus=1,
    node_risk_floor=(0, 0, 10, 30, 40),
    node_damage=(2, 2, 3, 4, 5),
    boss_actions={
        boss: tuple(
            tuple(
                replace(action, risk=20, damage=(3, 3, 4)[index])
                if action.id == "force"
                else replace(action, damage=(3, 3, 4)[index])
                for action in phase
            )
            for index, phase in enumerate(_boss_v2(boss))
        )
        for boss in BOSSES
    },
    summary=(
        "Узлы 3–5: минимальный риск каждого действия 10/30/40%; урон осложнения 3/4/5.",
        "Разведданные снижают риск, но на позднем маршруте безопасных проходов нет.",
        "Тревога добавляет 5 п.п. к обычному риску; «Тихий вход» снижает риск на 3 п.п.",
        "На узлах 4–5 плата разведданными дороже на 1. Урон осложнения по фазам финала: 3/3/4.",
        "Тревога 6: облава, урон 2 и тревога 4.",
    ),
)

RULESETS: dict[str, Ruleset] = {
    ROGUELITE_V1.version: ROGUELITE_V1,
    ROGUELITE_V2.version: ROGUELITE_V2,
    ROGUELITE_V3.version: ROGUELITE_V3,
}
DEFAULT_VERSION = ROGUELITE_V3.version


def rules(version: str) -> Ruleset:
    try:
        return RULESETS[version]
    except KeyError:
        raise ValueError(f"unknown death mission rules {version!r}") from None
