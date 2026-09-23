"""V4: whole-network escrow, specialist charges, real puzzle moves and safe practice."""

import hashlib
import json
import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
import pytest_asyncio

from spy_game import death_mission as engine
from spy_game.death_mission_challenges import next_step
from spy_game.death_mission_policy import RoutePolicy
from spy_game.death_mission_rewards import payout
from spy_game.death_mission_simulation import choose, reward_rules
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings
from test_death_mission import CHAT, NOW, begin, event, grant, launch, mutate


@pytest_asyncio.fixture
async def service(tmp_path):
    settings = SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "game.sqlite3",
        allowed_chat_ids=frozenset({CHAT}),
        allow_manual_spawn=True,
        death_mission_enabled=True,
    )
    result = SpyGameService(settings)
    await result.initialize(now=NOW)
    await result.enable_chat(CHAT, now=NOW)
    await grant(result, holdings={"informant": 12, "saboteur": 2, "ghost_agent": 1})
    await event(result)
    yield result
    await result.close()


def test_v3_trajectories_unchanged():
    digest = hashlib.sha256()
    for tactic in ("balanced", "stealth", "assault"):
        for index in range(100):
            seed = f"frozen-v3-20260922:{index}"
            state = engine.initial(seed, tactic, "roguelite_v3")
            step = 0
            while not state["outcome"]:
                actions = [a for a in engine.actions(state) if a.get("enabled", True)]
                action = actions[engine.roll("policy", f"{seed}:{step}") % len(actions)]["id"]
                state, _ = engine.advance(state, action, seed)
                step += 1
                # The saved percentage roll is presentation metadata; keep the
                # original frozen hash for every pre-existing gameplay field.
                baseline = dict(state, log=[{k: v for k, v in entry.items() if k != "roll"} for entry in state["log"]])
                digest.update(json.dumps(baseline, sort_keys=True, ensure_ascii=False).encode())
    assert digest.hexdigest() == "286fece923c0ff6b5d7e82ef1c92df4bbd00691cccfb3af66eaef10dac4046df"


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["preview", "armed", "in_run"])
@pytest.mark.parametrize("setting", ["enabled", "allowed_chat_ids"])
async def test_disabled_saved_tokens_cannot_reserve_and_active_stake_refunds_once(service, phase, setting):
    preview = await launch(service)
    token, state = preview.launch_token, preview
    if phase != "preview":
        state = await mutate(service, token, state, "arm", {"mode": "mission", "bonus": "none"})
    if phase == "in_run":
        state = await mutate(service, token, state, "commit")
    cfg = replace(service.settings, **{setting: False if setting == "enabled" else frozenset({CHAT - 1})})
    restarted = SpyGameService(cfg)
    await restarted.initialize(now=NOW)
    try:
        result = await mutate(
            restarted, token, state, "commit" if phase == "armed" else "arm", {"mode": "mission", "bonus": "none"}
        )
        assert result.status == ("cancelled_refunded" if phase == "in_run" else "expired")
        assert {a.agent_type: a.amount for a in await restarted.get_agents(1)} == {
            "informant": 12,
            "saboteur": 2,
            "ghost_agent": 1,
        }
        await restarted.get_death_mission(token, now=NOW)
        count = await restarted.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM death_mission_ledger").fetchone()[0]
        )
        assert count == (2 if phase == "in_run" else 0)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_whole_network_is_escrowed_and_support_does_not_stack(service):
    state = await launch(service)
    token = state.launch_token
    assert {a["id"]: a["amount"] for a in state.payload["stake"]} == {
        "informant": 12,
        "saboteur": 2,
        "ghost_agent": 1,
    }
    state = await mutate(service, token, state, "arm", {"mode": "mission", "bonus": "none"})
    results = await asyncio.gather(
        *(
            service.mutate_death_mission(
                token,
                action="commit",
                revision=state.payload["revision"],
                operation_id=f"commit-{i}",
                choice={},
                now=NOW,
            )
            for i in range(2)
        )
    )
    assert sum("error" not in result.payload for result in results) == 1
    state = next(result for result in results if "error" not in result.payload)
    assert state.status == "in_run" and not await service.get_agents(1)
    assert state.payload["mission"]["support"] == {"saboteur": True, "ghost_agent": True}
    await grant(service, holdings={"ghost_agent": 1, "informant": 2})
    await mutate(service, token, state, "abandon")
    assert {a.agent_type: a.amount for a in await service.get_agents(1)} == {"ghost_agent": 1, "informant": 2}
    assert (
        await service.database.read(lambda c: c.execute("SELECT COUNT(*) FROM death_mission_ledger").fetchone()[0]) == 2
    )


@pytest.mark.asyncio
async def test_inventory_spent_before_commit_requires_new_confirmation(service):
    state = await launch(service)
    token = state.launch_token
    state = await mutate(service, token, state, "arm", {"mode": "mission", "bonus": "none"})
    await service.database.transaction(
        lambda c: c.execute("UPDATE user_agents SET amount=2 WHERE user_id=1 AND agent_type='informant'"),
        immediate=True,
    )
    state = await mutate(service, token, state, "commit")
    assert state.status == "preview" and state.payload["error"] == "STALE_STAKE"
    assert next(a["amount"] for a in state.payload["stake"] if a["id"] == "informant") == 2
    assert (
        await service.database.read(lambda c: c.execute("SELECT COUNT(*) FROM death_mission_ledger").fetchone()[0]) == 0
    )


@pytest.mark.asyncio
async def test_back_from_all_in_restores_personal_reward_preview_without_shrinking_stake(service):
    state = await launch(service)
    token = state.launch_token
    expected_stake, expected_victory = state.payload["stake"], state.payload["victory"]
    state = await mutate(service, token, state, "arm", {"mode": "all_in", "bonus": "tier3"})
    assert state.payload["stake"] == expected_stake and state.payload["victory"] != expected_victory
    state = await mutate(service, token, state, "back")
    assert state.payload["stake"] == expected_stake and state.payload["victory"] == expected_victory


@pytest.mark.asyncio
async def test_telegram_puzzle_callbacks_roundtrip(service):
    from spy_game.death_mission_ui import decode, keyboard, text

    preview = await launch(service)
    state = engine.initial("telegram-puzzle", "balanced", specialists=("ghost_agent",))
    state["route"][0] = ["archive", "patrol"]
    state, _ = engine.advance(state, "archive", "telegram-puzzle")
    payload = dict(preview.payload, status="in_run", mission=engine.public_state(state))
    assert "Проведите сигнал" in text(payload)
    for row in keyboard(payload, "a" * 24).inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
            code = button.callback_data.split(".")[-1]
            if code.startswith("do_cell_"):
                action, choice = decode(code)
                assert action == "action" and choice["id"] in {a["id"] for a in payload["mission"]["actions"]}


def test_v4_protects_unused_shield_and_filters_useless_module():
    state = engine.initial("defence", "assault")
    state.update(phase="action", room="patrol", modules=["armor"])
    after, event = engine.resolve(state, "crawl", False)
    assert after["hp"] == 6 and after["assault_shield"] and event["absorbed"] == 1
    assert "escape" not in engine.module_pool("roguelite_v4", 3, [])
    assert "escape" in engine.module_pool("roguelite_v3", 3, [])


def test_specialist_action_consumes_charge_in_both_branches_and_changes_forecast():
    state = engine.initial("support", "balanced", specialists=("saboteur", "ghost_agent"))
    state.update(phase="boss", node=5, boss="train", hp=4, intel=1, boss_phase=2)
    actions = {a["id"]: a for a in engine.public_state(state)["actions"]}
    assert actions["plan:ghost_agent"]["risk"] == actions["plan"]["risk"] - 15
    assert actions["plan:ghost_agent"]["odds"] > actions["plan"]["odds"]
    for failed in (False, True):
        after, event = engine.resolve(state, "plan:ghost_agent", failed)
        assert after["support"] == {"saboteur": True, "ghost_agent": False}
        assert event["specialist"] == "ghost_agent"
    state.update(boss_phase=0, hp=6, intel=0)
    after, _ = engine.resolve(state, "force:saboteur", False)
    assert after["support"]["saboteur"] is False
    assert "force:saboteur" not in {a["id"] for a in engine.actions(after)}
    planner = RoutePolicy("roguelite_v4", "balanced", "train")
    assert (
        planner.position(engine.public_state(state, forecasts=False)).support
        != planner.position(engine.public_state(after, forecasts=False)).support
    )
    planner.close()


def test_archive_solutions_are_valid_and_reward_once_without_revealing_future_boards():
    for index in range(100):
        state = engine.initial(f"puzzle:{index}", "balanced")
        state["route"][0] = ["archive", "patrol"]
        state, _ = engine.advance(state, "archive", f"puzzle:{index}")
        view = engine.public_state(state)
        assert view["phase"] == "challenge" and "challenges" not in view and "route" not in view
        assert all(not layer["rooms"] for layer in view["map"][1:])
        with pytest.raises(ValueError):
            engine.advance(state, "cell_15", f"puzzle:{index}")
        while state["phase"] == "challenge":
            state, _ = engine.advance(state, next_step(state["challenge"]), f"puzzle:{index}")
        assert state["intel"] == 3 and state["node"] == 0 and state["room"] == "archive"
        assert state["log"][-1]["intel"] == 1
        engine.validate(state)
        with pytest.raises(ValueError):
            engine.advance(state, "cell_15", f"puzzle:{index}")


def test_simulation_skip_policy_really_skips_archive_reward():
    state = engine.initial("skip-puzzle", "balanced")
    state["route"][0] = ["archive", "patrol"]
    state, _ = engine.advance(state, "archive", "skip-puzzle")
    planner = RoutePolicy("roguelite_v4", "balanced", state["boss"], solve_challenges=False)
    try:
        action = choose(engine.public_state(state), "strong", planner=planner)
        assert action == "skip_puzzle"
        after, _ = engine.advance(state, action, "skip-puzzle")
        assert after["phase"] == "action" and after["intel"] == state["intel"]
    finally:
        planner.close()


@pytest.mark.asyncio
async def test_archive_restart_and_retry_keep_same_move_and_single_reward(service, monkeypatch):
    token, state = await begin(service, monkeypatch, seed="puzzle")

    def set_room(c):
        saved = json.loads(c.execute("SELECT state_json FROM death_mission_runs").fetchone()[0])
        saved["route"][0] = ["archive", "patrol"]
        c.execute("UPDATE death_mission_runs SET state_json=?", (json.dumps(saved),))

    await service.database.transaction(set_room, immediate=True)
    state = await mutate(service, token, state, "action", {"id": "archive"})
    restarted = SpyGameService(service.settings)
    await restarted.initialize(now=NOW)
    try:
        restored = await restarted.get_death_mission(token, now=NOW)
        assert restored.payload["mission"]["challenge"] == state.payload["mission"]["challenge"]
        while state.payload["mission"]["phase"] == "challenge":
            before = state
            choice = {"id": next_step(state.payload["mission"]["challenge"])}
            state = await mutate(restarted, token, before, "action", choice)
            replay = await mutate(restarted, token, before, "action", choice)
            assert replay.payload == state.payload
        assert state.payload["mission"]["intel"] == 3
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_practice_resumes_and_wins_without_events_escrow_or_achievements(service, monkeypatch):
    before = await service.get_agents(1)
    counts = lambda c: tuple(
        c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "game_events",
            "death_mission_runs",
            "death_mission_ledger",
            "death_mission_achievements",
            "event_history",
        )
    )
    initial_counts = await service.database.read(counts)
    with monkeypatch.context() as patch:
        patch.setattr("spy_game.death_mission_practice.secrets.token_hex", lambda size: "v4-win")
        practice = await service.missions.start_death_practice(
            user_id=1, chat_id=CHAT, username="a", display_name="a", now=NOW
        )
    token = practice.launch_token
    game_type, view = await service.get_html5_game(token, now=NOW)
    assert game_type == "death_operation" and view.payload["practice"]
    choice = {"id": view.payload["mission"]["actions"][0]["id"]}
    changed = await mutate(service, token, view, "action", choice)
    reopened = await service.missions.start_death_practice(
        user_id=1, chat_id=CHAT, username="a", display_name="a", now=NOW
    )
    assert reopened.payload == changed.payload and reopened.launch_token != token
    assert (await service.get_death_mission(token, now=NOW)).status == "not_found"
    final = reopened
    while final.status == "in_run":
        final = await mutate(
            service, reopened.launch_token, final, "action", {"id": choose(final.payload["mission"], "informed")}
        )
    assert final.status == "won" and final.payload["result"]["returned"] == []
    assert before == await service.get_agents(1)
    assert await service.database.read(counts) == initial_counts


@pytest.mark.asyncio
async def test_v4_complete_route_settles_whole_network_and_one_bonus_once(service, monkeypatch):
    await grant(service, holdings={"resident": 8})
    token, state = await begin(service, monkeypatch, seed="v4-win", bonus="tier4")
    while state.status == "in_run":
        previous = state
        choice = {"id": choose(state.payload["mission"], "informed")}
        state = await mutate(service, token, previous, "action", choice)
    assert state.status == "won"
    assert {a["id"]: a["amount"] for a in state.payload["result"]["returned"]} == {
        "informant": 14,
        "resident": 9,
        "saboteur": 2,
        "ghost_agent": 1,
    }
    assert sum(a["amount"] for a in state.payload["result"]["bonus"]) == 1
    replay = await mutate(service, token, previous, "action", choice)
    assert replay.payload == state.payload
    assert sum(a.amount for a in await service.get_agents(1)) == 27


def test_exact_v4_rewards_and_all_in_unchanged():
    rules = reward_rules("roguelite_v4")
    stake = {"informant": 5, "resident": 8, "ghost_agent": 1}
    back, bonus = payout(stake, "won", rules, "mission", "tier4", "payout")
    assert back == {"informant": 6, "resident": 9, "ghost_agent": 1}
    assert sum(bonus.values()) == 1
    assert payout(stake, "won", rules, "all_in", "tier3", "payout")[0] == {key: n * 2 for key, n in stake.items()}
