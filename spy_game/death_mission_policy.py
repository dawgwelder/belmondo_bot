"""Offline optimal policy over the entire observable Death Mission.

The objective is expected payout, including optional evacuation at every step.
Transitions use the live engine. Unseen rooms and module offers are integrated
over their distribution, never taken from a run's seed or hidden route.

This is a finite-horizon DP under the same independent uniform-roll model as
``finale_odds``. Hash rankings are modelled as uniform samples without replacement.
It is deliberately offline: evaluating a whole route is too expensive for a
request handler. One instance covers one ruleset/tactic/boss and payout ratio.
"""

from functools import lru_cache
from math import comb
from typing import NamedTuple

from . import death_mission as engine


class Position(NamedTuple):
    node: int
    phase: str
    room: str | None
    boss_phase: int
    hp: int
    intel: int
    alarm: int
    modules: tuple[str, ...]
    armor_used: bool
    assault_shield: bool
    passport_used: bool


def expected_best(values, offered):
    """E[max] for a uniform subset, without enumerating all subsets."""
    values = sorted(values)
    offered = min(offered, len(values))
    return sum(value * comb(index, offered - 1) for index, value in enumerate(values) if index >= offered - 1) / comb(
        len(values), offered
    )


class RoutePolicy:
    def __init__(self, version, tactic, boss, *, win_value=2.0, extraction_value=0.5):
        if win_value <= 0 or not 0 <= extraction_value <= win_value:
            raise ValueError("invalid payout objective")
        self.version, self.tactic, self.boss = version, tactic, boss
        self.rules = engine.rules(version)
        self.win_value = win_value
        self.extraction = extraction_value / win_value
        # Instance-local caches can be released between economic scenarios.
        self.value = lru_cache(maxsize=None)(self._value)

    def position(self, view):
        if (view["version"], view["tactic"], view["boss_id"]) != (self.version, self.tactic, self.boss):
            raise ValueError("policy does not match the public view")
        return Position(
            view["node"],
            view["phase"],
            view["room_id"],
            view["boss_phase"],
            view["hp"],
            view["intel"],
            view["alarm"],
            tuple(sorted(view["modules"])),
            view["armor_used"] if view["phase"] == "boss" else False,
            view["assault_shield"],
            view["passport_used"],
        )

    def state(self, position):
        state = position._asdict()
        state.update(
            version=self.version,
            tactic=self.tactic,
            boss=self.boss,
            modules=list(position.modules),
            route=[],
            offers=[],
            log=[],
            checkpoint=self.can_extract(position),
            survived_raid=False,
            outcome=None,
        )
        return state

    def can_extract(self, position):
        node = self.rules.escape_checkpoint_node if "escape" in position.modules else self.rules.checkpoint_node
        return position.node >= node

    @staticmethod
    def after(state):
        return Position(
            state["node"],
            state["phase"],
            state["room"],
            state["boss_phase"],
            state["hp"],
            state["intel"],
            state["alarm"],
            tuple(sorted(state["modules"])),
            state["armor_used"] if state["phase"] == "boss" else False,
            state["assault_shield"],
            state["passport_used"],
        )

    def action_value(self, position, action):
        if position.phase == "room":
            return self.value(position._replace(phase="action", room=action["id"], armor_used=False))
        if position.phase == "module":
            return self.value(position._replace(phase="room", modules=tuple(sorted((*position.modules, action["id"])))))
        state = self.state(position)
        total = 0.0
        risk = action["risk"] / 100
        for failed, weight in ((False, 1 - risk), (True, risk)):
            if weight == 0:
                continue
            after, _ = engine.resolve(state, action["id"], failed)
            continuation = (
                1.0 if after["outcome"] == "won" else 0.0 if after["outcome"] else self.value(self.after(after))
            )
            total += weight * continuation
        return total

    def _value(self, position):
        if position.phase == "room":
            pool = self.rules.node_rooms[position.node] or tuple(self.rules.rooms)
            values = {room: self.action_value(position, {"id": room}) for room in pool}
            if position.node in self.rules.shelter_nodes:
                # initial() guarantees shelter plus a uniform different room.
                shelter = self.action_value(position, {"id": "shelter"})
                others = [v for room, v in values.items() if room != "shelter"]
                best = sum(max(shelter, value) for value in others) / len(others)
            else:
                best = expected_best(values.values(), 2)
        elif position.phase == "module":
            best = expected_best(
                [
                    self.action_value(position, {"id": module})
                    for module in self.rules.modules
                    if module not in position.modules
                ],
                self.rules.module_offers,
            )
        else:
            best = max(
                self.action_value(position, action)
                for action in engine.actions(self.state(position))
                if action["enabled"]
            )
        return max(best, self.extraction) if self.can_extract(position) else best

    def choices(self, view):
        """Value of the actually offered choices, using only the public view."""
        position = self.position(view)
        result = {
            action["id"]: self.action_value(position, action) * self.win_value
            for action in view["actions"]
            if action.get("enabled", True)
        }
        if self.can_extract(position):
            result["extract"] = self.extraction * self.win_value
        return result

    def choose(self, view):
        choices = self.choices(view)
        # Prefer keeping a guaranteed payout over an exactly equal risky one.
        return max(choices, key=lambda action: (choices[action], action == "extract"))

    def close(self):
        self.value.cache_clear()
