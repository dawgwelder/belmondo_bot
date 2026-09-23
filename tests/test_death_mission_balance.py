"""Economic, public-information and backward-compatibility regressions."""

import hashlib
import json
from itertools import combinations

import pytest

from spy_game import death_mission as engine
from spy_game.death_mission_policy import RoutePolicy, expected_best
from spy_game.death_mission_rewards import bonus_options, payout
from spy_game.death_mission_simulation import Objective, economic_gate, reward_rules, weighted


@pytest.mark.parametrize(
    "version,digest",
    [
        ("roguelite_v1", "54584c7bf48a348e564e5c47317fc38caf59059b3de1fcef11121a45e47d3608"),
        ("roguelite_v2", "fad99fb3d5670abc25e9005d8f859f109069c62d8f9e31e41c707f8bdc6882c9"),
    ],
)
def test_legacy_trajectories_match_frozen_152ee01(version, digest):
    # Frozen independently using the engine/content from commit 152ee01.
    actual = hashlib.sha256()
    for tactic in ("balanced", "stealth", "assault"):
        for index in range(100):
            seed = f"frozen-152ee01:{index}"
            state = engine.initial(seed, tactic, version)
            step = 0
            while not state["outcome"]:
                options = [a for a in engine.actions(state) if a.get("enabled", True)]
                action = options[engine.roll("policy", f"{seed}:{step}") % len(options)]["id"]
                state, _ = engine.advance(state, action, seed)
                step += 1
                # Ignore only the new display metadata; all original state and
                # event fields must still match the independently frozen engine.
                baseline = dict(state, log=[{k: v for k, v in entry.items() if k != "roll"} for entry in state["log"]])
                actual.update(json.dumps(baseline, sort_keys=True, ensure_ascii=False).encode())
    assert actual.hexdigest() == digest


@pytest.mark.parametrize("version", ["roguelite_v1", "roguelite_v2"])
@pytest.mark.parametrize("bonus,amount", [("tier3", 2), ("tier4", 1)])
def test_existing_versions_keep_their_unrestricted_bonus(version, bonus, amount):
    returned, awarded = payout({"informant": 1}, "won", reward_rules(version), "mission", bonus, "legacy")
    assert returned == {"informant": 2}
    assert sum(awarded.values()) == amount


def test_v3_bonus_uses_staked_agents_of_the_required_or_higher_tier():
    options = {o["id"]: o for o in bonus_options("roguelite_v3", {"analyst": 4, "resident": 1, "informant": 1000})}
    assert not options["tier3"]["locked"]
    assert options["tier4"]["locked"]
    assert options["tier3"]["amount"] == 1
    assert not options["none"]["locked"]
    with pytest.raises(ValueError, match="not available"):
        payout({"informant": 1000}, "won", reward_rules("roguelite_v3"), "mission", "tier4", "invalid")


def test_exact_rounding_bonus_and_all_in_are_shared_with_settlement():
    rules = reward_rules("roguelite_v3")
    stake = {"informant": 5, "analyst": 5, "resident": 5}
    back, bonus = payout(stake, "extracted", rules, "mission", "tier4", "extract")
    assert back == {"informant": 2, "analyst": 2, "resident": 2} and bonus == {}
    returned, bonus = payout(stake, "won", rules, "mission", "tier4", "win")
    assert returned == {"informant": 10, "analyst": 10, "resident": 10}
    assert len(bonus) == 1 and sum(bonus.values()) == 1
    objective = Objective.actual("test", stake, "tier4", rules)
    assert objective.win == (weighted(returned) + weighted(bonus)) / weighted(stake)
    assert objective.extraction == weighted(back) / weighted(stake)
    _, all_in_bonus = payout({"informant": 1}, "won", rules, "all_in", "tier3", "all-in")
    assert sum(all_in_bonus.values()) == 1
    assert set(all_in_bonus) <= set(rules["tier3"])


def test_uniform_offer_expectation_matches_exhaustive_enumeration():
    values = [0.1, 0.6, 0.2, 0.6, 0.9, 0.4]
    for offered in (2, 3):
        subsets = list(combinations(values, offered))
        assert expected_best(values, offered) == pytest.approx(sum(max(s) for s in subsets) / len(subsets))


def test_dp_optimises_payout_with_evacuations_and_does_not_use_display_rounding():
    state = engine.initial("policy", "balanced", "roguelite_v1")
    state.update(phase="boss", node=5, boss="train", boss_phase=2, hp=1, intel=0, alarm=4, checkpoint=True)
    view = engine.public_state(state)
    policy = RoutePolicy("roguelite_v1", "balanced", "train")
    # At 80% risk, force is worth 0.2 * 2 = 0.4; extraction is worth 0.5.
    assert policy.choices(view) == pytest.approx({"force": 0.4, "extract": 0.5})
    view["odds"] = 99  # An inaccurate rounded UI forecast must not affect the DP.
    assert policy.choose(view) == "extract"
    # A single odd agent yields nothing on evacuation; playing is then better.
    tiny_stake = RoutePolicy("roguelite_v1", "balanced", "train", extraction_value=0)
    assert tiny_stake.choose(view) == "force"
    policy.close()
    tiny_stake.close()


def test_public_observation_reveals_defence_usage_but_no_unseen_offers():
    state = engine.initial("secret-seed", "assault")
    state.update(passport_used=True, armor_used=True)
    for forecasts in (False, True):
        view = engine.public_state(state, forecasts=forecasts)
        assert view["passport_used"] is True and view["armor_used"] is True and view["assault_shield"] is True
        assert not {"seed", "route", "offers"} & view.keys()
        assert "secret-seed" not in json.dumps(view)
        assert {a["id"] for a in view["actions"]} == set(state["route"][0])


def test_dp_values_scanner_for_future_rooms_even_without_a_finale_delta():
    state = engine.initial("lookahead", "balanced", "roguelite_v2")
    state.update(phase="module", node=1, boss="train", hp=6, intel=2, offers=["scanner", "escape"])
    view = engine.public_state(state)
    assert view["actions"][0]["odds"] == view["actions"][1]["odds"]
    policy = RoutePolicy("roguelite_v2", "balanced", "train")
    try:
        values = policy.choices(view)
        assert values["scanner"] > values["escape"]
    finally:
        policy.close()


def test_gate_rejects_insufficient_or_incomplete_evidence_and_uncertain_overruns():
    row = dict(
        policy="strong",
        tactic="balanced",
        objective="test",
        total_return_ratio=1.1,
        total_return_ci95=[1.05, 1.16],
        model_expected_return_by_boss={"train": 1.1, "hq": 1.09},
    )
    assert not economic_gate([row], 10000, 1.15, complete=True)["passed"]
    row["total_return_ci95"] = [1.05, 1.14]
    assert economic_gate([row], 10000, 1.15, complete=True)["passed"]
    assert not economic_gate([row], 1000, 1.15, complete=True)["passed"]
    assert not economic_gate([row], 10000, 1.15, complete=False)["passed"]
    row["model_expected_return_by_boss"]["train"] = 1.16
    assert not economic_gate([row], 10000, 1.15, complete=True)["passed"]
