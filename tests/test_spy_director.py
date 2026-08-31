from pathlib import Path

import pytest

from spy_game.director import LLMDirector, ResilientDirector, RuleBasedDirector
from spy_game.models import DirectorState
from spy_game.settings import SpySettings


class FixedRandom:
    def __init__(self, value=1):
        self.value = value

    def randint(self, _start, _end):
        return self.value


def settings(tmp_path: Path) -> SpySettings:
    return SpySettings(
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
