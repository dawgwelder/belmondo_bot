from pathlib import Path
from dataclasses import replace
import random

import pytest

from spy_game.director import (
    LLMDirector,
    ResilientDirector,
    RuleBasedDirector,
    selection_candidates,
    selection_weights,
)
from spy_game.models import DirectorState
from spy_game.settings import SpySettings


class FixedRandom:
    def __init__(self, value=1):
        self.value = value

    def randint(self, _start, _end):
        return self.value


class EndRandom:
    def randint(self, _start, end):
        return end


def settings(tmp_path: Path) -> SpySettings:
    return SpySettings(
        llm_mole_enabled=False,
        mode="dev",
        enabled=True,
        database_path=tmp_path / "spy.sqlite3",
        allowed_chat_ids=frozenset({-100}),
    )


def director_state(config: SpySettings) -> DirectorState:
    return DirectorState(
        chat_id=-100,
        activity_score=18,
        active_players=4,
        minutes_since_last_event=42,
        recent_events=("recruitment", "dead_drop"),
        story_arc="mole_hunt",
        story_stage=1,
        allowed_events=tuple(item.event_type for item in config.event_weights),
    )


@pytest.mark.asyncio
async def test_llm_director_accepts_only_structured_allowed_decision(tmp_path):
    config = settings(tmp_path)
    state = director_state(config)
    captured = {}

    async def request(prompt, validator, *, corrective_hint):
        captured["prompt"] = prompt
        captured["hint"] = corrective_hint
        return validator(
            {
                "event_type": "cooperative_operation",
                "tone": "paranoid",
                "story_hook": "section_7",
                "intensity": 2,
            }
        )

    decision = await LLMDirector(request=request).choose_event(state)

    assert decision.event_type == "cooperative_operation"
    assert decision.story_hook == "section_7"
    assert "<untrusted_json>" in captured["prompt"]
    assert "allowed_events" in captured["prompt"]
    assert "четыре ключа" in captured["hint"]


def test_llm_director_rejects_unknown_mechanics(tmp_path):
    state = director_state(settings(tmp_path))
    invalid = {
        "event_type": "give_everyone_100_agents",
        "tone": "paranoid",
        "story_hook": None,
        "intensity": 2,
    }
    assert LLMDirector._validate(invalid, state) is None


@pytest.mark.asyncio
async def test_llm_director_failure_uses_rule_based_fallback(tmp_path):
    config = settings(tmp_path)
    state = director_state(config)

    class BrokenDirector:
        async def choose_event(self, _state):
            raise TimeoutError

    director = ResilientDirector(
        BrokenDirector(),
        RuleBasedDirector(config, FixedRandom(1)),
    )
    decision = await director.choose_event(state)
    assert decision.event_type == "recruitment"


@pytest.mark.asyncio
async def test_rule_director_uses_find_mole_from_stage_three(tmp_path):
    config = settings(tmp_path)
    early_decision = await RuleBasedDirector(config, EndRandom()).choose_event(
        director_state(config)
    )
    state = DirectorState(
        **{
            **director_state(config).__dict__,
            "recent_events": (),
            "story_stage": 3,
        }
    )

    decision = await RuleBasedDirector(config, FixedRandom(1)).choose_event(state)
    later_state = DirectorState(**{**state.__dict__, "story_stage": 4})
    later_decision = await RuleBasedDirector(config, EndRandom()).choose_event(
        later_state
    )

    assert early_decision.event_type != "find_mole"
    assert decision.event_type == "find_mole"
    assert decision.story_hook == "mole_hunt"
    assert later_decision.event_type == "find_mole"


@pytest.mark.asyncio
async def test_non_recruitment_can_follow_another_non_recruitment(tmp_path):
    config = settings(tmp_path)
    state = replace(
        director_state(config), recent_events=("dead_drop",), story_arc=None
    )
    decision = await RuleBasedDirector(config, EndRandom()).choose_event(state)
    assert decision.event_type not in ("recruitment", "find_mole")


def test_history_constraints_preserve_recruitment_and_story_without_locking_rotation(
    tmp_path,
):
    state = director_state(settings(tmp_path))
    assert selection_candidates(
        replace(state, recent_events=("chase", "handler", "intercept"))
    ) == ("recruitment",)
    assert "chase" not in selection_candidates(
        replace(state, recent_events=("chase", "chase"))
    )
    assert selection_candidates(replace(state, story_stage=3, recent_events=())) == (
        "find_mole",
    )
    assert (
        len(
            selection_candidates(
                replace(state, story_stage=3, recent_events=("find_mole",))
            )
        )
        > 1
    )
    assert "find_mole" not in selection_candidates(replace(state, story_stage=2))
    assert selection_candidates(
        replace(state, allowed_events=("chase",), recent_events=("chase",) * 3)
    ) == ("chase",)


def test_recent_events_lose_weight_and_group_play_depends_on_activity(tmp_path):
    config = settings(tmp_path)
    state = replace(director_state(config), story_arc=None, recent_events=())
    baseline = dict(selection_weights(config, state))
    repeated = dict(
        selection_weights(config, replace(state, recent_events=("recruitment",)))
    )
    assert repeated["recruitment"] < baseline["recruitment"]
    quiet = dict(selection_weights(config, replace(state, active_players=1)))
    assert quiet["cooperative_operation"] < baseline["cooperative_operation"]


@pytest.mark.asyncio
async def test_llm_cannot_bypass_server_history_constraints(tmp_path):
    config = settings(tmp_path)
    state = replace(
        director_state(config), recent_events=("chase", "handler", "intercept")
    )

    async def request(prompt, validator, **_kwargs):
        assert '"weights": {"recruitment":' in prompt
        # Even a transport that skips its validator cannot bypass constraints.
        return {
            "event_type": "chase",
            "tone": "serious",
            "story_hook": None,
            "intensity": 1,
        }

    director = ResilientDirector(
        LLMDirector(request=request, settings=config),
        RuleBasedDirector(config, EndRandom()),
    )
    assert (await director.choose_event(state)).event_type == "recruitment"
    assert (
        LLMDirector._validate(
            {"event_type": [], "tone": "serious", "story_hook": None, "intensity": 1},
            state,
        )
        is None
    )


@pytest.mark.asyncio
async def test_tone_is_independent_of_event_draw(tmp_path):
    config = settings(tmp_path)
    decisions = []

    class Draws:
        def __init__(self, tone):
            self.draws = iter((1, tone))

        def randint(self, start, end):
            result = next(self.draws)
            assert start <= result <= end
            return result

    for tone in range(4):
        decisions.append(
            await RuleBasedDirector(config, Draws(tone)).choose_event(
                director_state(config)
            )
        )
    assert len({decision.event_type for decision in decisions}) == 1
    assert len({decision.tone for decision in decisions}) == 4


@pytest.mark.asyncio
async def test_long_rotation_has_no_triples_or_recruitment_drought(tmp_path):
    config = settings(tmp_path)
    state = replace(director_state(config), story_arc=None, recent_events=())
    director = RuleBasedDirector(config, random.Random(20260917))
    history = []
    for _ in range(1000):
        decision = await director.choose_event(
            replace(state, recent_events=tuple(reversed(history[-5:])))
        )
        history.append(decision.event_type)
        if len(history) >= 3:
            assert len(set(history[-3:])) > 1
        if len(history) >= 4:
            assert "recruitment" in history[-4:]
    assert set(history) == set(state.allowed_events) - {"find_mole"}
    assert any(
        a != "recruitment" and b != "recruitment" for a, b in zip(history, history[1:])
    )
