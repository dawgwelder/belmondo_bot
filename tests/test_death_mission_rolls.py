"""Percentage disclosure must preserve draws, trajectories and saved settlements."""

import hashlib
import json

import pytest

from spy_game import death_mission as engine
from spy_game.service import SpyGameService
from test_death_mission import NOW, begin, mutate
from test_death_mission_v4 import service  # noqa: F401 -- shared temporary SQLite fixture


@pytest.mark.parametrize(
    "version,expected",
    [
        ("roguelite_v1", "3fbf8493dfaba5a353a616ca95005a390a8b13510a987732315b9072bf42ab87"),
        ("roguelite_v2", "d7e81840e5e3d781c4045b5f115ccc18950546129439147b54c79d2c58754f65"),
        ("roguelite_v3", "b4b933bc3f91803636ea3c56af025c5536d76e0d4ea707bbe53ad0c637e6ae00"),
        ("roguelite_v4", "6294227dec98dfca8ec79b4c69c8ef848105f0938cbb032269db5de712d1df44"),
    ],
)
def test_percentage_metadata_preserves_frozen_gameplay(version, expected):
    # Captured from the working tree BEFORE adding percentage disclosure.
    digest = hashlib.sha256()
    for tactic in ("balanced", "stealth", "assault"):
        for index in range(25):
            seed = f"percent-reveal-baseline:{index}"
            state = engine.initial(seed, tactic, version, specialists=tuple(engine.SPECIALISTS))
            step = 0
            while not state["outcome"]:
                actions = [a for a in engine.actions(state) if a.get("enabled", True)]
                action = actions[engine.roll("policy", f"{seed}:{step}") % len(actions)]["id"]
                state, _ = engine.advance(state, action, seed)
                baseline = dict(state, log=[{k: v for k, v in e.items() if k != "roll"} for e in state["log"]])
                digest.update(json.dumps(baseline, sort_keys=True, ensure_ascii=False).encode())
                step += 1
    assert digest.hexdigest() == expected


@pytest.mark.parametrize("raw,failed", [(0, True), (24, True), (25, False), (99, False)])
def test_scale_boundaries_match_existing_complication(raw, failed, monkeypatch):
    state = engine.initial("percent", "balanced")
    state.update(phase="action", room="patrol")
    original = engine.roll
    calls = []

    def fixed(seed, key, *args, **kwargs):
        if key.startswith("action:"):
            calls.append(key)
            return raw
        return original(seed, key, *args, **kwargs)

    monkeypatch.setattr(engine, "roll", fixed)
    after, events = engine.advance(state, "rush", "percent")
    check = events[0]
    assert calls == ["action:0:0:rush"]
    assert check["roll"] == raw + 1
    assert check["risk"] == 25 and check["failed"] is failed
    assert (check["roll"] <= check["risk"]) is failed
    assert after["log"][-1] == check
    view = engine.public_state(after)
    assert view["events"][-1]["roll"] == raw + 1
    assert f"бросок {raw + 1}/100" in view["log"][-1]
    assert not {"seed", "route", "challenges"} & view.keys()
    assert all("roll" not in action for action in view["actions"])


def test_deterministic_action_has_no_draw_or_percentage_reveal(monkeypatch):
    state = engine.initial("safe", "balanced")
    state.update(phase="action", room="patrol", node=1)
    monkeypatch.setattr(engine, "roll", lambda *args, **kwargs: pytest.fail("safe action rolled"))
    _, events = engine.advance(state, "bypass", "safe")
    assert events[0]["risk"] == 0 and "roll" not in events[0]


@pytest.mark.asyncio
async def test_saved_roll_survives_replay_and_restart(service, monkeypatch):
    token, state = await begin(service, monkeypatch, seed="saved-percent")

    def set_patrol(connection):
        saved = json.loads(connection.execute("SELECT state_json FROM death_mission_runs").fetchone()[0])
        saved["route"][0] = ["patrol", "shelter"]
        connection.execute("UPDATE death_mission_runs SET state_json=?", (json.dumps(saved),))

    await service.database.transaction(set_patrol, immediate=True)
    state = await mutate(service, token, state, "action", {"id": "patrol"})
    before = state
    state = await mutate(service, token, before, "action", {"id": "rush"})
    check = state.payload["mission"]["events"][-1]
    assert 1 <= check["roll"] <= 100
    restarted = SpyGameService(service.settings)
    await restarted.initialize(now=NOW)
    try:
        restored = await restarted.get_death_mission(token, now=NOW)
        assert restored.payload["mission"]["events"][-1] == check
        replay = await mutate(restarted, token, before, "action", {"id": "rush"})
        assert replay.payload == state.payload
    finally:
        await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [0, 34, 35, 99])
async def test_all_in_scale_reflects_existing_draw_and_replay(service, monkeypatch, raw):
    original = engine.roll
    monkeypatch.setattr(
        engine,
        "roll",
        lambda seed, key, *args, **kwargs: raw if key == "all_in" else original(seed, key, *args, **kwargs),
    )
    token, state = await begin(service, monkeypatch, mode="all_in")
    assert state.status == ("won" if raw < 35 else "lost")
    check = state.payload["result"]["check"]
    assert check["roll"] == 100 - raw and check["risk"] == 65
    assert check["failed"] is (raw >= 35)
    replay = await service.mutate_death_mission(
        token,
        action="commit",
        revision=1,
        operation_id="op_1_commit",
        choice={},
        now=NOW,
    )
    assert replay.payload == state.payload
