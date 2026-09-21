"""Reproducible training and balance report; no database or real rewards.

python -m spy_game.death_mission_simulation --runs 10000 --seed beta-v1
python -m spy_game.death_mission_simulation --version roguelite_v1 --runs 4000

Policies only see ``engine.public_state``: no seed, no hidden route. ``strong``
uses the finale odds the UI shows to players, so its return is what an
informed player can realistically extract; that number feeds the economic gate.
"""

import argparse
import json
from collections import Counter

from . import death_mission as engine

POLICIES = ("random", "aggressive", "careful", "strong")
MODULE_ORDER = ["armor", "silencer", "medic", "passport", "scanner", "escape"]


def _room_rank(state, room):
    hp, intel, alarm = state["hp"], state["intel"], state["alarm"]
    return {
        "shelter": 6 if hp <= 4 or alarm >= 4 else 0,
        "archive": 5 if intel <= 3 else 1,
        "cache": 3,
        "contact": 4 if hp >= 5 and intel <= 3 else 1,
        "patrol": 0,
        "ambush": -1,
    }[room]


def choose(state, policy, random_index=0):
    """Pick an action id from the public view. Deterministic given inputs."""
    actions = [a for a in state["actions"] if a.get("enabled", True)]
    if policy == "random":
        return actions[random_index % len(actions)]["id"]
    if state["phase"] == "room":
        if policy == "strong":
            return max(actions, key=lambda a: _room_rank(state, a["id"]))["id"]
        ranks = {
            "archive": 5 if state["intel"] < 4 else 1,
            "shelter": 6 if state["hp"] < 5 else 0,
            "cache": 3,
            "contact": 2,
            "patrol": 0,
            "ambush": -1,
        }
        return max(actions, key=lambda a: ranks[a["id"]])["id"]
    if state["phase"] == "module":
        if policy == "strong":
            return max(
                actions,
                key=lambda a: (a.get("odds") or 0, -MODULE_ORDER.index(a["id"])),
            )["id"]
        return min(actions, key=lambda a: MODULE_ORDER.index(a["id"]))["id"]
    if policy == "aggressive":
        return max(actions, key=lambda a: a["intel"] + a["risk"] / 100 - a["cost"])[
            "id"
        ]

    def value(a):
        hp_need = 1.5 if state["hp"] <= 3 else 1
        intel_need = 1.2 if state["intel"] < 4 else 0.4
        return (
            min(a["hp"], state["max_hp"] - state["hp"]) * hp_need
            + (min(a["intel"], state["max_intel"] - state["intel"]) - a["cost"])
            * intel_need
            - a["risk"] / 100 * a["damage"] * hp_need
            - a["alarm"] * 0.3
        )

    if policy == "strong":
        # Maximise the shown finale odds; break ties by resource value.
        return max(actions, key=lambda a: (a.get("odds") or 0, value(a)))["id"]
    if state["phase"] == "boss":
        # Careful: prepared route, else a safe deterministic option, else force.
        def survives(a):
            preview = a.get("preview") or {}
            return not (preview.get("success") or {}).get("dead", False)

        for pick in ("plan",):
            action = next((a for a in actions if a["id"] == pick), None)
            if action and survives(action):
                return pick
        safe = [a for a in actions if a["risk"] == 0 and survives(a)]
        if safe:
            return safe[0]["id"]
        return "force" if any(a["id"] == "force" for a in actions) else actions[0]["id"]
    return max(actions, key=value)["id"]


def should_extract(view, policy):
    """Policy-side evacuation rule; the engine never extracts by itself."""
    if not view["checkpoint"]:
        return False
    if policy == "careful":
        return view["hp"] <= 2
    if policy == "strong":
        odds = view.get("odds") or 0
        if view["phase"] == "boss":
            return odds < 25
        # Two nodes may still heal or arm the group; be less eager early.
        return odds < (12 if view["node"] <= 3 else 20)
    return False


def _finale_key(view):
    return f"finale{view['boss_phase'] + 1}"


def simulate(count, prefix, version=engine.DEFAULT_VERSION, gate=1.15):
    ruleset = engine.rules(version)
    report = {
        "rules": version,
        "seed_prefix": prefix,
        "runs_per_policy_tactic": count,
        "all_in_return_ratio": 0.70,
        "results": [],
    }
    for tactic in ruleset.tactics:
        for policy in POLICIES:
            outcomes, lengths = Counter(), []
            deaths, extractions = Counter(), Counter()
            risky, failed = 0, 0
            entry = []
            for index in range(count):
                seed = f"{prefix}:{index}"
                state = engine.initial(seed, tactic, version)
                step = 0
                reached_finale = False
                while not state["outcome"]:
                    view = engine.public_state(state)
                    if view["phase"] == "boss" and not reached_finale:
                        reached_finale = True
                        entry.append(
                            (view["hp"], view["intel"], view["alarm"], view["odds"])
                        )
                    if should_extract(view, policy):
                        state["outcome"] = "extracted"
                        extractions[
                            _finale_key(view) if view["phase"] == "boss" else f"node{view['node']}"
                        ] += 1
                        break
                    action = choose(
                        view, policy, engine.roll(f"policy:{seed}", str(step), 100000)
                    )
                    before = view
                    state, _events = engine.advance(state, action, seed)
                    step += 1
                    event = state["log"][-1] if state["log"] and isinstance(state["log"][-1], dict) else None
                    if event and event["action"] == action and event["risk"]:
                        risky += 1
                        failed += int(event["failed"])
                    if state["outcome"] == "lost":
                        deaths[
                            _finale_key(before) if before["phase"] == "boss" else f"node{before['node'] + 1}"
                        ] += 1
                    if step > 40:
                        raise RuntimeError("non-terminating route")
                outcomes[state["outcome"]] += 1
                lengths.append(step)
            finale = {
                "reached_percent": round(100 * len(entry) / count, 2),
            }
            if entry:
                finale.update(
                    mean_hp=round(sum(e[0] for e in entry) / len(entry), 2),
                    mean_intel=round(sum(e[1] for e in entry) / len(entry), 2),
                    mean_alarm=round(sum(e[2] for e in entry) / len(entry), 2),
                    mean_odds=round(sum(e[3] for e in entry) / len(entry), 2),
                    hp_histogram={
                        str(hp): c for hp, c in sorted(Counter(e[0] for e in entry).items())
                    },
                )
            report["results"].append(
                dict(
                    tactic=tactic,
                    policy=policy,
                    counts=dict(outcomes),
                    win_percent=round(100 * outcomes["won"] / count, 2),
                    extract_percent=round(100 * outcomes["extracted"] / count, 2),
                    mean_actions=round(sum(lengths) / count, 2),
                    stake_return_ratio=round(
                        (2 * outcomes["won"] + 0.5 * outcomes["extracted"]) / count, 4
                    ),
                    deaths_by_node=dict(sorted(deaths.items())),
                    extractions_by_node=dict(sorted(extractions.items())),
                    complication_percent=round(100 * failed / risky, 2) if risky else 0,
                    finale_entry=finale,
                )
            )
    strong = max(
        (r for r in report["results"] if r["policy"] == "strong"),
        key=lambda r: r["stake_return_ratio"],
    )
    report["economic_gate"] = dict(
        policy="strong",
        tactic=strong["tactic"],
        stake_return_ratio=strong["stake_return_ratio"],
        limit=gate,
        passed=strong["stake_return_ratio"] <= gate,
        note=(
            "Return of the best informed policy on the stake alone, without the "
            "Tier bonus and without per-type rounding. all_in_v1 returns 0.70."
        ),
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--seed", default="beta-v1")
    parser.add_argument("--version", default=engine.DEFAULT_VERSION)
    parser.add_argument(
        "--gate",
        type=float,
        default=1.15,
        help="maximum stake return ratio allowed for the strong policy",
    )
    args = parser.parse_args()
    if args.runs <= 0:
        parser.error("--runs must be positive")
    if args.version not in engine.RULESETS:
        parser.error(f"unknown rules version; known: {sorted(engine.RULESETS)}")
    print(
        json.dumps(
            simulate(args.runs, args.seed, args.version, args.gate),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
