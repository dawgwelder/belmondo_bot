"""Reproducible training and balance report; no database or real rewards.

python -m spy_game.death_mission_simulation --runs 10000 --seed beta-v1
"""

import argparse
import json
from collections import Counter

from . import death_mission as engine


def choose(state, policy, random_index=0):
    # A policy sees exactly the public view, not the private route or seed.
    actions = [a for a in state["actions"] if a.get("enabled", True)]
    if policy == "random":
        return actions[random_index % len(actions)]["id"]
    if state["phase"] == "room":
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
        order = ["armor", "silencer", "medic", "scanner", "passport", "escape"]
        return min(actions, key=lambda a: order.index(a["id"]))["id"]
    if policy == "aggressive":
        return max(actions, key=lambda a: a["intel"] + a["risk"] / 100 - a["cost"])[
            "id"
        ]

    def value(a):
        hp_need = 1.5 if state["hp"] <= 3 else 1
        intel_need = 1.2 if state["intel"] < 4 else 0.4
        return (
            min(a["hp"], 6 - state["hp"]) * hp_need
            + (min(a["intel"], 6 - state["intel"]) - a["cost"]) * intel_need
            - a["risk"] / 100 * a["damage"] * hp_need
            - a["alarm"] * 0.3
        )

    if state["phase"] == "boss":
        # Prefer the prepared route if we can afford it without dying.
        plan = next((a for a in actions if a["id"] == "plan"), None)
        return "plan" if plan and state["hp"] + plan["hp"] > 0 else "force"
    return max(actions, key=value)["id"]


def simulate(count, prefix):
    report = {
        "rules": engine.VERSION,
        "seed_prefix": prefix,
        "runs_per_policy_tactic": count,
        "results": [],
    }
    for tactic in engine.TACTICS:
        for policy in ("random", "aggressive", "careful"):
            outcomes, lengths = Counter(), []
            for index in range(count):
                seed = f"{prefix}:{index}"
                state = engine.initial(seed, tactic)
                step = 0
                while not state["outcome"]:
                    view = engine.public_state(state)
                    if policy == "careful" and state["checkpoint"] and state["hp"] <= 2:
                        state["outcome"] = "extracted"
                        break
                    action = choose(
                        view, policy, engine.roll(f"policy:{seed}", str(step), 100000)
                    )
                    state = engine.advance(state, action, seed)
                    step += 1
                    if step > 30:
                        raise RuntimeError("non-terminating route")
                outcomes[state["outcome"]] += 1
                lengths.append(step)
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
                )
            )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--seed", default="beta-v1")
    args = parser.parse_args()
    if args.runs <= 0:
        parser.error("--runs must be positive")
    print(json.dumps(simulate(args.runs, args.seed), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
