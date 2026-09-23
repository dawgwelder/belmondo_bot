"""Reproducible offline balance report, using the production engine and payouts.

python -m spy_game.death_mission_simulation --runs 10000 --seed validation-v4

``strong`` optimises expected payout over the entire route, including evacuation
and unseen offers. ``informed`` is the former finale-greedy heuristic. No policy
receives the seed or the hidden route. Reports include exact per-type rounding,
bonus issuance, analytical weights, confidence intervals and a full-payout gate.
"""

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass
from statistics import median

from . import death_mission as engine
from .death_mission_policy import RoutePolicy
from .death_mission_challenges import next_step
from .death_mission_rewards import bonus_options, bonus_spec, payout, returned_stake
from .settings import AGENT_TYPES, SpySettings

POLICIES = ("random", "aggressive", "careful", "informed", "strong")
MODULE_ORDER = ["armor", "silencer", "medic", "passport", "scanner", "escape"]
# Fixed before validation; analytical weights, not an exchange price.
TIER_WEIGHTS = {tier: 4 ** (tier - 1) for tier in range(1, 7)}
STAKES = {
    "single": {"informant": 1},
    "small_mixed": {"informant": 5, "operative": 3, "analyst": 1},
    "rare": {"analyst": 5, "resident": 5},
    "large": {
        "informant": 1000,
        "operative": 250,
        "analyst": 50,
        "resident": 10,
        "ghost_agent": 2,
        "intelligence_director": 1,
    },
}
V4_STAKES = {
    "single": {"informant": 1},
    "small_mixed": {"informant": 5, "operative": 3, "analyst": 1},
    "rare": {"resident": 8},
    "specialists": {"resident": 8, "saboteur": 1, "ghost_agent": 1},
    "full_basic": {"informant": 10},
    "large": STAKES["large"],
}
MIN_VALIDATION_RUNS = 10_000


def weighted(bundle):
    return sum(amount * TIER_WEIGHTS[AGENT_TYPES[agent].tier] for agent, amount in bundle.items())


def reward_rules(version):
    settings = SpySettings  # dataclass defaults; no live configuration or database
    return dict(
        version=version,
        multiplier=settings.death_operation_reward_multiplier,
        all_in_percent=settings.death_operation_success_percent,
        tier3=list(settings.death_operation_bonus_pool),
        tier4=list(settings.death_mission_tier4_pool),
    )


def interval(total, total_squares, count):
    mean = total / count
    variance = max(0.0, (total_squares - total * total / count) / (count - 1)) if count > 1 else 0.0
    radius = 1.96 * math.sqrt(variance / count)
    return [round(mean - radius, 6), round(mean + radius, 6)]


@dataclass(frozen=True)
class Objective:
    name: str
    win: float = 2.0
    extraction: float = 0.5
    stake: dict | None = None
    bonus: str = "none"
    specialists: tuple[str, ...] = ()
    solve_challenges: bool = True

    @classmethod
    def actual(cls, name, stake, bonus, rules):
        pool, amount = bonus_spec(rules, stake, "mission", bonus)
        bonus_value = sum(TIER_WEIGHTS[AGENT_TYPES[a].tier] for a in pool) / len(pool) * amount if pool else 0
        value = weighted(stake)
        return cls(
            name,
            weighted(
                returned_stake(stake, "won", rules["multiplier"], ratio=engine.rules(rules["version"]).mission_return)
            )
            / value
            + bonus_value / value,
            weighted(returned_stake(stake, "extracted", rules["multiplier"])) / value,
            stake,
            bonus,
            tuple(key for key in engine.SPECIALISTS if stake.get(key, 0) > 0),
        )


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


def choose(state, policy, random_index=0, *, planner=None):
    """Pick using the public observation only; strong requires an offline DP."""
    if policy == "strong":
        if planner is None:
            raise ValueError("strong policy requires a RoutePolicy")
        return planner.choose(state)
    if state["phase"] == "challenge":
        return next_step(state["challenge"])
    if policy not in POLICIES:
        raise ValueError(f"unknown policy {policy!r}")
    actions = [a for a in state["actions"] if a.get("enabled", True)]
    if policy == "random":
        return actions[random_index % len(actions)]["id"]
    if state["phase"] == "room":
        if policy == "informed":
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
        if policy == "informed":
            return max(actions, key=lambda a: (a.get("odds") or 0, -MODULE_ORDER.index(a["id"])))["id"]
        return min(actions, key=lambda a: MODULE_ORDER.index(a["id"]))["id"]
    if policy == "aggressive":
        return max(actions, key=lambda a: a["intel"] + a["risk"] / 100 - a["cost"])["id"]

    def value(action):
        hp_need = 1.5 if state["hp"] <= 3 else 1
        intel_need = 1.2 if state["intel"] < 4 else 0.4
        return (
            min(action["hp"], state["max_hp"] - state["hp"]) * hp_need
            + (min(action["intel"], state["max_intel"] - state["intel"]) - action["cost"]) * intel_need
            - action["risk"] / 100 * action["damage"] * hp_need
            - action["alarm"] * 0.3
        )

    if policy == "informed":
        return max(actions, key=lambda a: (a.get("odds") or 0, value(a)))["id"]
    if state["phase"] == "boss":

        def survives(action):
            preview = action.get("preview")
            if preview:
                return not preview["success"]["dead"]
            # Ordinary damage may be absorbed, but this conservative fallback
            # never treats a guaranteed fatal health cost as safe.
            return state["hp"] + action["hp"] > 0

        plan = next((a for a in actions if a["id"] == "plan"), None)
        if plan and survives(plan):
            return "plan"
        safe = [a for a in actions if a["risk"] == 0 and survives(a)]
        if safe:
            return safe[0]["id"]
        return "force" if any(a["id"] == "force" for a in actions) else actions[0]["id"]
    return max(actions, key=value)["id"]


def should_extract(view, policy):
    if not view["checkpoint"]:
        return False
    if policy == "careful":
        return view["hp"] <= 2
    if policy == "informed":
        odds = view.get("odds") or 0
        return odds < (25 if view["phase"] == "boss" else 12 if view["node"] <= 3 else 20)
    return False  # The strong policy includes evacuation in its DP.


def _location(state):
    return f"finale{state['boss_phase'] + 1}" if state["phase"] == "boss" else f"node{state['node'] + 1}"


def _sample(count, prefix, version, tactic, policy, objective):
    rules = reward_rules(version)
    outcomes, deaths, extractions, picks, causes, builds, bosses = (Counter() for _ in range(7))
    lengths, entries = [], []
    returned, awarded = Counter(), Counter()
    risky = failed = 0
    total = squares = 0.0
    planners = {}
    model_values = {}
    try:
        if policy == "strong":
            for boss in engine.rules(version).bosses:
                planner = RoutePolicy(
                    version,
                    tactic,
                    boss,
                    win_value=objective.win,
                    extraction_value=objective.extraction,
                    solve_challenges=objective.solve_challenges,
                )
                planners[boss] = planner
                start = engine.initial("distribution-only", tactic, version, specialists=objective.specialists)
                start["boss"] = boss
                model_values[boss] = (
                    planner.value(planner.position(engine.public_state(start, forecasts=False))) * objective.win
                )
        for index in range(count):
            seed = f"{prefix}:{index}"
            state = engine.initial(seed, tactic, version, specialists=objective.specialists)
            step = 0
            entered = False
            while not state["outcome"]:
                view = engine.public_state(state, forecasts=policy == "informed")
                if state["phase"] == "boss" and not entered:
                    entered = True
                    entries.append((state["hp"], state["intel"], state["alarm"]))
                action = (
                    "skip_puzzle"
                    if view["phase"] == "challenge" and not objective.solve_challenges
                    else "extract"
                    if should_extract(view, policy)
                    else choose(
                        view,
                        policy,
                        engine.roll(f"policy:{seed}", str(step), 100000),
                        planner=planners.get(state["boss"]),
                    )
                )
                if action == "extract":
                    state["outcome"] = "extracted"
                    extractions[f"after_node{state['node']}" if state["phase"] != "boss" else _location(state)] += 1
                    break
                location = _location(state)
                context = state["boss"] if state["phase"] == "boss" else state["room"] or state["phase"]
                picks[f"{location}.{context}.{action}"] += 1
                state, events = engine.advance(state, action, seed)
                step += 1
                if events and events[-1].get("kind") == "action":
                    event = events[-1]
                    if event["risk"]:
                        risky += 1
                        failed += int(event["failed"])
                    if state["outcome"] == "lost":
                        deaths[location] += 1
                        causes["raid" if event["raid"] else "complication" if event["failed"] else "health_cost"] += 1
                # Policies use no private history. Keep simulation transitions cheap.
                state["log"] = []
                if step > 60:
                    raise RuntimeError("non-terminating route")
            outcome = state["outcome"]
            outcomes[outcome] += 1
            bosses[f"{state['boss']}.{outcome}"] += 1
            builds["+".join(sorted(state["modules"])) or "none"] += 1
            lengths.append(step)
            if objective.stake is not None:
                back, bonus = payout(objective.stake, outcome, rules, "mission", objective.bonus, seed)
                returned.update(back)
                awarded.update(bonus)
                value = weighted(back) + weighted(bonus)
                value /= weighted(objective.stake)
            else:
                value = objective.win if outcome == "won" else objective.extraction if outcome == "extracted" else 0
            total += value
            squares += value * value
    finally:
        for planner in planners.values():
            planner.close()
    result = dict(
        tactic=tactic,
        policy=policy,
        objective=objective.name,
        counts=dict(outcomes),
        win_percent=round(100 * outcomes["won"] / count, 2),
        extract_percent=round(100 * outcomes["extracted"] / count, 2),
        mean_actions=round(sum(lengths) / count, 2),
        median_actions=median(lengths),
        p95_actions=sorted(lengths)[math.ceil(count * 0.95) - 1],
        stake_return_ratio=round(
            (objective.win * outcomes["won"] + objective.extraction * outcomes["extracted"]) / count, 6
        ),
        total_return_ratio=round(total / count, 6),
        total_return_ci95=interval(total, squares, count),
        deaths_by_node=dict(sorted(deaths.items())),
        deaths_by_cause=dict(causes),
        extractions_by_node=dict(sorted(extractions.items())),
        actions=dict(sorted(picks.items())),
        module_builds=dict(sorted(builds.items())),
        outcomes_by_boss=dict(sorted(bosses.items())),
        complication_percent=round(100 * failed / risky, 2) if risky else 0,
        finale_entry=dict(reached_percent=round(100 * len(entries) / count, 2)),
        specialists=list(objective.specialists),
        puzzle_skill="perfect" if objective.solve_challenges else "skip",
    )
    if entries:
        for index, resource in enumerate(("hp", "intel", "alarm")):
            result["finale_entry"][f"mean_{resource}"] = round(sum(e[index] for e in entries) / len(entries), 3)
            result["finale_entry"][f"{resource}_histogram"] = dict(sorted(Counter(e[index] for e in entries).items()))
    if model_values:
        result["model_expected_return_by_boss"] = model_values
        result["model_expected_return"] = sum(model_values.values()) / len(model_values)
    if objective.stake is not None:
        result.update(
            stake=objective.stake,
            bonus=objective.bonus,
            returned_by_type=dict(sorted(returned.items())),
            bonus_by_type=dict(sorted(awarded.items())),
        )
        result["stake_return_ratio"] = round(weighted(returned) / (count * weighted(objective.stake)), 6)
        by_tier = {}
        for tier in TIER_WEIGHTS:
            initial = sum(n for a, n in objective.stake.items() if AGENT_TYPES[a].tier == tier) * count
            back = sum(n for a, n in returned.items() if AGENT_TYPES[a].tier == tier)
            bonus = sum(n for a, n in awarded.items() if AGENT_TYPES[a].tier == tier)
            by_tier[tier] = dict(staked=initial, returned=back, bonus=bonus, net=back + bonus - initial)
        result["by_tier"] = by_tier
        all_in_bonus = sum(TIER_WEIGHTS[AGENT_TYPES[a].tier] for a in rules["tier3"]) / len(rules["tier3"])
        result["all_in_expected_return_ratio"] = (
            rules["all_in_percent"] / 100 * (rules["multiplier"] + all_in_bonus / weighted(objective.stake))
        )
    return result


def economic_gate(rows, count, limit, *, complete):
    relevant = [r for r in rows if r["policy"] == "strong"]
    worst = max(relevant, key=lambda r: r["total_return_ci95"][1], default=None)
    model_upper = max((max(r["model_expected_return_by_boss"].values()) for r in relevant), default=None)
    enough = count >= MIN_VALIDATION_RUNS
    passed = bool(complete and enough and worst and worst["total_return_ci95"][1] <= limit and model_upper <= limit)
    return dict(
        policy="strong",
        limit=limit,
        passed=passed,
        complete=complete,
        sufficient_samples=enough,
        minimum_runs_per_case=MIN_VALIDATION_RUNS,
        worst_case=None
        if worst is None
        else dict(
            tactic=worst["tactic"],
            objective=worst["objective"],
            ratio=worst["total_return_ratio"],
            ci95=worst["total_return_ci95"],
        ),
        model_upper_bound=model_upper,
        scope="Full personal-mission payout including bonus and per-type rounding; all-in is a separate unchanged mode.",
        model="Whole-route optimal expected payout, uniform independent rolls/offers, optional evacuation; no seed or future route.",
        statistical_rule="Every case: upper endpoint of its 95% interval and exact model bound must be <= limit. Intervals are per-case, not simultaneous.",
    )


def simulate(
    count, prefix, version=engine.DEFAULT_VERSION, gate=1.15, *, policies=POLICIES, include_economy=True, progress=None
):
    if count <= 0:
        raise ValueError("runs must be positive")
    ruleset = engine.rules(version)
    rules = reward_rules(version)
    win = ruleset.mission_return[0] / ruleset.mission_return[1] if ruleset.mission_return else rules["multiplier"]
    tasks = [(policy, Objective("stake_only", win=win)) for policy in policies]
    if include_economy and "strong" in policies:
        if ruleset.bonus_min_agents:
            # One bonus per threshold agents of at least that tier is worth <=S/threshold
            # for ANY positive nondecreasing tier weights. Extraction <=S/2.
            max_bonus = max(ruleset.tier3_bonus, ruleset.tier4_bonus) / ruleset.bonus_min_agents
            supports = (
                [()] if not ruleset.specialists else [(), ("saboteur",), ("ghost_agent",), ("saboteur", "ghost_agent")]
            )
            for support in supports:
                name = "all_stakes_bound" + (":" + "+".join(support) if support else "")
                tasks.append(("strong", Objective(name, win=win + max_bonus, specialists=support)))
            if ruleset.archive_challenge:
                tasks.append(
                    (
                        "strong",
                        Objective(
                            "all_stakes_bound:skip_puzzles",
                            win=win + max_bonus,
                            specialists=tuple(engine.SPECIALISTS),
                            solve_challenges=False,
                        ),
                    )
                )
        for name, stake in (V4_STAKES if ruleset.specialists else STAKES).items():
            options = [b for b in bonus_options(version, stake) if not b["locked"]]
            # When a free bonus is available, choosing none is dominated.
            if any(b["amount"] for b in options):
                options = [b for b in options if b["amount"]]
            tasks += [("strong", Objective.actual(f"{name}:{b['id']}", stake, b["id"], rules)) for b in options]
    results, economy = [], []
    for tactic in ruleset.tactics:
        for policy, objective in tasks:
            if progress:
                progress(f"{version}: {tactic} / {policy} / {objective.name} ({count} runs)")
            row = _sample(count, prefix, version, tactic, policy, objective)
            (results if objective.name == "stake_only" else economy).append(row)
    return dict(
        rules=version,
        seed_prefix=prefix,
        runs_per_policy_tactic=count,
        tier_weights=TIER_WEIGHTS,
        weights_note="Analytical weights, not a player-facing exchange rate; fixed before validation.",
        reward_rules=rules,
        results=results,
        economy=economy,
        economic_gate=economic_gate(results + economy, count, gate, complete=include_economy and "strong" in policies),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--seed", default="validation-v4")
    parser.add_argument("--version", choices=sorted(engine.RULESETS), default=engine.DEFAULT_VERSION)
    parser.add_argument("--gate", type=float, default=1.15, help="maximum full-payout return ratio, including bonuses")
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=POLICIES)
    parser.add_argument(
        "--no-economy", action="store_true", help="quick gameplay sample; cannot pass the economic gate"
    )
    args = parser.parse_args()
    if args.runs <= 0:
        parser.error("--runs must be positive")
    report = simulate(
        args.runs,
        args.seed,
        args.version,
        args.gate,
        policies=args.policies,
        include_economy=not args.no_economy,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["economic_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
