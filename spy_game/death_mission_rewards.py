"""Pure, version-pinned payout rules shared by settlement and the simulator."""

from . import death_mission as engine
from .settings import AGENT_TYPES


def bonus_options(version, stake):
    ruleset = engine.rules(version)
    options = []
    if ruleset.bonus_min_agents:
        options.append(
            dict(
                id="none",
                name="Без дополнительного агента",
                amount=0,
                locked=False,
                description=(
                    "Победа возвращает отряд и по одному агенту за каждые пять одного типа."
                    if ruleset.mission_return
                    else "Победа удваивает ставку."
                ),
            )
        )
    for tier, amount in ((3, ruleset.tier3_bonus), (4, ruleset.tier4_bonus)):
        count = sum(n for agent, n in stake.items() if AGENT_TYPES[agent].tier >= tier)
        requirement = ruleset.bonus_min_agents
        options.append(
            dict(
                id=f"tier{tier}",
                name=f"Агент Tier {tier} ×{amount}",
                amount=amount,
                locked=count < requirement,
                description=(
                    f"Нужно {requirement} агентов Tier {tier} или выше в ставке; сейчас {count}."
                    if requirement
                    else f"Один случайный тип Tier {tier}, ×{amount}."
                ),
            )
        )
    return options


def returned_stake(stake, outcome, multiplier, *, checkpoint=False, ratio=None):
    if outcome == "won":
        if ratio:
            numerator, denominator = ratio
            return {agent: amount * numerator // denominator for agent, amount in stake.items()}
        return {agent: amount * multiplier for agent, amount in stake.items()}
    if outcome == "cancelled_refunded":
        return dict(stake)
    if outcome == "extracted" or (outcome == "timed_out" and checkpoint):
        return {agent: amount // 2 for agent, amount in stake.items() if amount // 2}
    return {}


def bonus_spec(rules, stake, mode, choice):
    """Return an eligible bonus pool and count; legacy all-in stays unchanged."""
    if mode == "all_in":
        return rules["tier3"], 1
    option = next((o for o in bonus_options(rules.get("version", engine.VERSION), stake) if o["id"] == choice), None)
    if not option or option["locked"]:
        raise ValueError("bonus is not available for this stake")
    return (rules[choice], option["amount"]) if option["amount"] else ([], 0)


def payout(stake, outcome, rules, mode, choice, seed, *, checkpoint=False):
    ratio = engine.rules(rules.get("version", engine.VERSION)).mission_return if mode == "mission" else None
    returned = returned_stake(stake, outcome, rules["multiplier"], checkpoint=checkpoint, ratio=ratio)
    bonus = {}
    if outcome == "won":
        pool, amount = bonus_spec(rules, stake, mode, choice)
        if amount:
            agent = pool[engine.roll(seed, "bonus", len(pool), version=rules.get("version", engine.VERSION))]
            bonus[agent] = amount
    return returned, bonus
