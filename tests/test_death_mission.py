import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web

import handlers.spy_game as spy_handlers

from spy_game import death_mission as engine
from spy_game.death_mission_simulation import choose
from spy_game.death_mission_ui import keyboard, publish_pending
from spy_game.models import DeathOperationStatus
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings
from spy_game.webapp import SpyWebAppServer, SpyWebAppSettings


NOW = datetime.now(timezone.utc)
CHAT = -100123


async def grant(service, user=1, holdings=None):
    await service.get_profile(
        user_id=user, username=f"agent{user}", display_name="PRIVATE NAME", now=NOW
    )

    def operation(connection):
        for agent, amount in (holdings or {"informant": 5, "analyst": 1}).items():
            connection.execute(
                "INSERT INTO user_agents VALUES(?,?,?) ON CONFLICT(user_id,agent_type) DO UPDATE SET amount=amount+excluded.amount",
                (user, agent, amount),
            )

    await service.database.transaction(operation, immediate=True)


async def event(service, chat=CHAT, message=100):
    result = await service.manual_spawn(chat, event_type="death_operation", now=NOW)
    assert result.event.config_id == "death_choice_v1"
    await service.attach_message(result.event.event_id, message)
    return result.event


async def launch(service, user=1, chat=CHAT, message=100, now=NOW):
    return await service.start_death_mission(
        chat_id=chat,
        message_id=message,
        user_id=user,
        username=f"agent{user}",
        display_name="PRIVATE NAME",
        now=now,
    )


async def mutate(service, token, state, action, choice=None, key=None, now=NOW):
    return await service.mutate_death_mission(
        token,
        action=action,
        revision=state.payload["revision"],
        operation_id=key or f"op_{state.payload['revision']}_{action}",
        choice=choice or {},
        now=now,
    )


async def begin(
    service,
    monkeypatch,
    *,
    mode="mission",
    bonus="tier3",
    seed="win",
    user=1,
    chat=CHAT,
    message=100,
):
    preview = await launch(service, user=user, chat=chat, message=message)
    token = preview.launch_token
    armed = await mutate(
        service, token, preview, "arm", dict(mode=mode, tactic="balanced", bonus=bonus)
    )
    assert armed.status == "armed", armed.payload
    with monkeypatch.context() as patch:
        patch.setattr(
            "spy_game.death_mission_repository.secrets.token_hex", lambda size: seed
        )
        started = await mutate(service, token, armed, "commit")
    return token, started


async def play(service, token, state, *, stop_at_checkpoint=False):
    while state.status == "in_run":
        if stop_at_checkpoint and state.payload["mission"]["checkpoint"]:
            break
        action = choose(state.payload["mission"], "careful")
        state = await mutate(service, token, state, "action", {"id": action})
    return state


async def counts(service):
    return await service.database.read(
        lambda c: dict(
            c.execute(
                "SELECT action,COUNT(*) FROM death_mission_ledger GROUP BY action"
            ).fetchall()
        )
    )


@pytest_asyncio.fixture
async def service(tmp_path):
    config = SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "game.sqlite3",
        allowed_chat_ids=frozenset({CHAT, CHAT - 1}),
        allow_manual_spawn=True,
        death_mission_enabled=True,
        death_operation_success_percent=100,
    )
    service = SpyGameService(config)
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT, now=NOW)
    await service.enable_chat(CHAT - 1, now=NOW)
    await grant(service)
    await event(service)
    yield service
    await service.close()


@pytest.mark.asyncio
async def test_entry_preview_and_arm_never_spend_and_mode_can_change(service):
    state = await launch(service)
    assert state.status == "preview"
    before = await service.get_agents(1)
    armed = await mutate(service, state.launch_token, state, "arm", {"mode": "all_in"})
    changed = await mutate(
        service, state.launch_token, armed, "arm", {"mode": "mission", "bonus": "tier4"}
    )
    assert changed.payload["mode"] == "mission"
    assert changed.payload["bonus"] == "tier4"
    assert before == await service.get_agents(1)
    assert await counts(service) == {}


@pytest.mark.asyncio
async def test_all_in_settles_immediately_and_replay_cannot_double_reward(
    service, monkeypatch
):
    token, state = await begin(service, monkeypatch, mode="all_in")
    assert state.status == "won"
    assert state.payload["mission"] == {}
    assert sum(a["amount"] for a in state.payload["result"]["bonus"]) == 1
    assert sum(a["amount"] for a in state.payload["result"]["returned"]) == 12
    replay = await service.mutate_death_mission(
        token,
        action="commit",
        revision=1,
        operation_id="op_1_commit",
        choice={},
        now=NOW,
    )
    assert replay.payload == state.payload
    assert await counts(service) == {"reserve": 1, "settle": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("bonus,tier,amount", [("tier3", 3, 2), ("tier4", 4, 1)])
async def test_full_mission_pays_selected_higher_bonus_and_keeps_later_income(
    service, monkeypatch, bonus, tier, amount
):
    token, state = await begin(service, monkeypatch, bonus=bonus, seed="0")
    assert state.status == "in_run"
    assert await service.get_agents(1) == ()
    assert sum(a["amount"] for a in await service.reserved_mission_agents(1)) == 6
    await grant(service, holdings={"operative": 3})
    result = await play(service, token, state)
    assert result.status == "won", result.payload
    earned = result.payload["result"]["bonus"]
    assert sum(a["amount"] for a in earned) == amount
    assert all(service.settings.agent_tier(a["id"]) == tier for a in earned)
    holdings = {a.agent_type: a.amount for a in await service.get_agents(1)}
    assert holdings["operative"] == 3
    assert holdings["informant"] == 10
    assert result.payload["progress"]["checkpoint"] == 1
    assert result.payload["progress"]["won"] == 1
    assert await service.reserved_mission_agents(1) == []
    assert await counts(service) == {"reserve": 1, "settle": 1}


@pytest.mark.asyncio
async def test_changed_stake_invalidates_commit_without_reserve(service):
    state = await launch(service)
    token = state.launch_token
    armed = await mutate(service, token, state, "arm", {"mode": "mission"})
    await grant(service, holdings={"informant": 1})
    result = await mutate(service, token, armed, "commit")
    assert result.status == "preview"
    assert result.payload["error"] == "STALE_STAKE"
    assert await counts(service) == {}


@pytest.mark.asyncio
async def test_two_modes_compete_for_one_event(service, monkeypatch):
    await grant(service, user=2)
    p1, p2 = await launch(service), await launch(service, user=2)
    a1 = await mutate(service, p1.launch_token, p1, "arm", {"mode": "mission"})
    a2 = await mutate(service, p2.launch_token, p2, "arm", {"mode": "all_in"})
    results = await asyncio.gather(
        mutate(service, p1.launch_token, a1, "commit"),
        mutate(service, p2.launch_token, a2, "commit"),
    )
    assert sorted(r.status for r in results) == ["in_run", "lost_race"]
    assert sum(a.amount for a in await service.get_agents(2)) == 6
    assert await counts(service) == {"reserve": 1}


@pytest.mark.asyncio
async def test_global_run_blocks_new_all_in_in_other_chat_even_with_new_agents(
    service, monkeypatch
):
    await begin(service, monkeypatch)
    await grant(service, holdings={"informant": 2})
    await event(service, chat=CHAT - 1, message=200)
    state = await launch(service, chat=CHAT - 1, message=200)
    armed = await mutate(service, state.launch_token, state, "arm", {"mode": "all_in"})
    result = await mutate(service, state.launch_token, armed, "commit")
    assert result.payload["error"] == "RUN_IN_PROGRESS"
    assert await counts(service) == {"reserve": 1}


@pytest.mark.asyncio
async def test_two_tabs_one_revision_and_conflicting_key(service, monkeypatch):
    token, state = await begin(service, monkeypatch)
    choice = {"id": state.payload["mission"]["actions"][0]["id"]}
    first, second = await asyncio.gather(
        mutate(service, token, state, "action", choice, key="a"),
        mutate(service, token, state, "action", choice, key="b"),
    )
    assert first.payload["revision"] == state.payload["revision"] + 1
    assert second.payload["error"] == "STALE_REVISION"
    conflict = await mutate(service, token, state, "action", {"id": "other"}, key="a")
    assert conflict.payload["error"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_reopen_and_restart_preserve_run_and_revoke_old_token(
    service, monkeypatch
):
    token, state = await begin(service, monkeypatch)
    reopened = await launch(service, now=NOW + timedelta(seconds=2))
    assert reopened.payload == state.payload
    assert (await service.get_death_mission(token, now=NOW)).status == "not_found"
    other = SpyGameService(service.settings)
    await other.initialize(now=NOW + timedelta(seconds=3))
    try:
        assert (
            await other.get_death_mission(reopened.launch_token, now=NOW)
        ).payload == state.payload
    finally:
        await other.close()


@pytest.mark.asyncio
async def test_extraction_and_timeout_return_same_rounded_stake(service, monkeypatch):
    token, state = await begin(service, monkeypatch, seed="beta")
    state = await play(service, token, state, stop_at_checkpoint=True)
    assert state.status == "in_run"
    assert state.payload["mission"]["checkpoint"]
    timeout = await service.get_death_mission(token, now=NOW + timedelta(minutes=16))
    assert timeout.status == "timed_out"
    assert timeout.payload["result"]["returned"] == state.payload["extraction"]
    assert {a.agent_type: a.amount for a in await service.get_agents(1)} == {
        "informant": 2
    }
    assert await counts(service) == {"reserve": 1, "settle": 1}


@pytest.mark.asyncio
async def test_timeout_before_checkpoint_and_repeated_generic_expiry(
    service, monkeypatch
):
    token, _ = await begin(service, monkeypatch)
    await service.tick(now=NOW + timedelta(minutes=16))
    await service.tick(now=NOW + timedelta(minutes=17))
    assert (await service.get_death_mission(token, now=NOW)).status == "timed_out"
    assert await service.get_agents(1) == ()
    history = await service.database.read(
        lambda c: c.execute("SELECT COUNT(*) FROM event_history").fetchone()[0]
    )
    assert history == 1


@pytest.mark.asyncio
async def test_disable_chat_refunds_stake_and_does_not_award_progress(
    service, monkeypatch
):
    token, _ = await begin(service, monkeypatch)
    await service.disable_chat(CHAT, now=NOW)
    state = await service.get_death_mission(token, now=NOW)
    assert state.status == "cancelled_refunded"
    assert sum(a.amount for a in await service.get_agents(1)) == 6
    assert state.payload["progress"] == {}
    assert await counts(service) == {"reserve": 1, "settle": 1}


@pytest.mark.asyncio
async def test_sql_failure_rolls_back_whole_settlement(service, monkeypatch):
    token, state = await begin(service, monkeypatch)
    await service.database.transaction(
        lambda c: c.execute(
            "CREATE TRIGGER reject_settle BEFORE INSERT ON event_history BEGIN SELECT RAISE(ABORT, 'no'); END;"
        ),
        immediate=True,
    )
    with pytest.raises(sqlite3.IntegrityError):
        await mutate(service, token, state, "abandon")
    assert (await service.get_death_mission(token, now=NOW)).payload == state.payload
    assert await counts(service) == {"reserve": 1}
    assert await service.get_agents(1) == ()


@pytest.mark.asyncio
async def test_legacy_callback_cannot_resolve_choice_event(service):
    event_id = await service.database.read(
        lambda c: c.execute("SELECT id FROM game_events").fetchone()[0]
    )
    result = await service.run_death_operation(
        event_id=event_id,
        action="death",
        chat_id=CHAT,
        user_id=1,
        username="agent",
        display_name="Name",
        now=NOW,
    )
    assert result.status is DeathOperationStatus.INVALID_ACTION
    assert await counts(service) == {}


@pytest.mark.asyncio
async def test_telegram_and_html5_share_revision_and_owner_authorization(
    service, monkeypatch
):
    token, state = await begin(service, monkeypatch)
    run_id = await service.death_mission_run_id(token)
    choice = {"id": state.payload["mission"]["actions"][0]["id"]}
    arguments = dict(
        run_id=run_id,
        chat_id=CHAT,
        message_id=100,
        action="action",
        revision=state.payload["revision"],
        operation_id="telegram",
        choice=choice,
        now=NOW,
    )
    forbidden = await service.mission_callback(user_id=2, **arguments)
    assert forbidden.status == "forbidden"
    applied = await service.mission_callback(user_id=1, **arguments)
    assert applied.payload == (await service.get_death_mission(token, now=NOW)).payload
    markup = keyboard(applied.payload, run_id)
    assert all(
        len(button.callback_data.encode()) <= 64
        for row in markup.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_outbox_retries_failed_delivery_without_repeating_economy(
    service, monkeypatch
):
    await begin(service, monkeypatch, mode="all_in")
    bot = SimpleNamespace(
        edit_message_text=AsyncMock(side_effect=[RuntimeError("offline"), None])
    )
    await publish_pending(service, bot)
    await publish_pending(service, bot)
    assert bot.edit_message_text.await_count == 1
    await service.database.transaction(
        lambda connection: connection.execute(
            "UPDATE death_mission_outbox SET next_attempt_at=NULL"
        ),
        immediate=True,
    )
    await publish_pending(service, bot)
    await publish_pending(service, bot)
    assert bot.edit_message_text.await_count == 2
    assert "@agent1" in bot.edit_message_text.call_args.kwargs["text"]
    assert "PRIVATE NAME" not in bot.edit_message_text.call_args.kwargs["text"]
    assert await counts(service) == {"reserve": 1, "settle": 1}


@pytest.mark.asyncio
async def test_http_modes_validation_secret_filter_and_wrong_game(service):
    state = await launch(service)
    server = SpyWebAppServer(
        service,
        "123:test",
        SpyWebAppSettings(enabled=True, game_url="https://example.test/spy-app/game/"),
    )
    request = SimpleNamespace(
        headers={"X-Spy-Game-Token": state.launch_token},
        match_info={"action": "arm"},
        json=AsyncMock(
            return_value={
                "revision": 0,
                "operation_id": "http1",
                "choice": {"mode": "mission", "bonus": "tier4"},
            }
        ),
    )
    response = await server.game_death_action(request)
    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["status"] == "armed"
    assert payload["bonus"] == "tier4"
    assert all(key not in payload for key in ("seed", "user_id", "token_hash", "route"))
    assert "PRIVATE NAME" not in response.text
    request.json = AsyncMock(
        return_value={"revision": True, "operation_id": "bad", "choice": {}}
    )
    with pytest.raises(web.HTTPBadRequest):
        await server.game_death_action(request)
    request.headers = {"X-Spy-Game-Token": "invalid"}
    with pytest.raises(web.HTTPUnauthorized):
        await server.game_death_action(request)


def test_engine_replay_is_deterministic_and_every_route_terminates():
    for index in range(100):
        seed = str(index)
        state = engine.initial(seed, "balanced")
        assert all(len(layer) == 2 for layer in state["route"])
        for _ in range(25):
            if state["outcome"]:
                break
            action = choose(engine.public_state(state), "careful")
            first = engine.advance(state, action, seed)
            assert first == engine.advance(state, action, seed)
            state = first
            assert 0 <= state["hp"] <= 6
            assert 0 <= state["intel"] <= 6
            assert 0 <= state["alarm"] < 6
        assert state["outcome"] in {"won", "lost"}


def test_death_in_last_phase_takes_priority_over_victory():
    state = engine.initial("last", "balanced")
    state.update(phase="boss", boss="train", boss_phase=2, hp=1, intel=6, node=5)
    result = engine.advance(state, "plan", "last")
    assert result["outcome"] == "lost"
    assert result["hp"] == 0


@pytest.mark.asyncio
async def test_extraction_racing_timeout_settles_once(service, monkeypatch):
    token, state = await begin(service, monkeypatch, seed="beta")
    state = await play(service, token, state, stop_at_checkpoint=True)
    extraction, timeout = await asyncio.gather(
        mutate(service, token, state, "extract"),
        service.get_death_mission(token, now=NOW + timedelta(minutes=16)),
    )
    assert extraction.status == timeout.status == "extracted"
    assert await counts(service) == {"reserve": 1, "settle": 1}
    assert extraction.payload["result"]["returned"] == state.payload["extraction"]


@pytest.mark.asyncio
async def test_confirmation_expires_and_locked_tactic_is_not_accepted(service):
    preview = await launch(service)
    token = preview.launch_token
    invalid = await mutate(
        service,
        token,
        preview,
        "arm",
        {"mode": "mission", "tactic": "stealth"},
        key="locked",
    )
    assert invalid.payload["error"] == "INVALID_ACTION"
    armed = await mutate(service, token, preview, "arm", {"mode": "mission"})
    expired = await mutate(
        service, token, armed, "commit", now=NOW + timedelta(seconds=60)
    )
    assert expired.payload["error"] == "CONFIRMATION_EXPIRED"
    assert await counts(service) == {}


@pytest.mark.asyncio
async def test_invalid_persisted_state_refunds_on_restart(service, monkeypatch):
    token, _ = await begin(service, monkeypatch)
    await service.database.transaction(
        lambda c: c.execute("UPDATE death_mission_runs SET state_json='broken'"),
        immediate=True,
    )
    restarted = SpyGameService(service.settings)
    await restarted.initialize(now=NOW + timedelta(minutes=16))
    try:
        state = await restarted.get_death_mission(token, now=NOW)
        assert state.status == "cancelled_refunded"
        assert sum(a.amount for a in await restarted.get_agents(1)) == 6
        assert await counts(restarted) == {"reserve": 1, "settle": 1}
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_flag_off_preserves_existing_event_and_run_rules(service, monkeypatch):
    token, started = await begin(service, monkeypatch)
    disabled = SpyGameService(
        replace(
            service.settings,
            death_mission_enabled=False,
            death_operation_reward_multiplier=3,
        )
    )
    await disabled.initialize(now=NOW)
    try:
        reopened = await launch(disabled)
        assert reopened.payload == started.payload
        assert reopened.payload["rules"]["multiplier"] == 2
        abandoned = await mutate(disabled, reopened.launch_token, reopened, "abandon")
        assert abandoned.status == "lost"
        created = await disabled.manual_spawn(
            CHAT, event_type="death_operation", now=NOW
        )
        assert created.event.config_id == "all_in_v1"
    finally:
        await disabled.close()


@pytest.mark.asyncio
async def test_three_completed_checkpoints_unlock_tactic_once(service, monkeypatch):
    for index in range(3):
        message = 100 + index
        if index:
            await event(service, message=message)
        token, started = await begin(service, monkeypatch, seed="0", message=message)
        result = await play(service, token, started)
        assert result.status == "won"
        again = await service.get_death_mission(token, now=NOW)
        assert again.payload["progress"]["checkpoint"] == index + 1
    assert "stealth" in [t["id"] for t in result.payload["tactics"]]


@pytest.mark.asyncio
async def test_all_in_failure_loses_only_stake(service, monkeypatch):
    service.repository.settings = replace(
        service.settings, death_operation_success_percent=0
    )
    _, result = await begin(service, monkeypatch, mode="all_in")
    assert result.status == "lost"
    assert result.payload["result"]["bonus"] == []
    assert await service.get_agents(1) == ()


@pytest.mark.asyncio
async def test_start_rollback_does_not_reserve_event_or_agents(service):
    preview = await launch(service)
    armed = await mutate(
        service, preview.launch_token, preview, "arm", {"mode": "mission"}
    )
    await service.database.transaction(
        lambda c: c.execute(
            "CREATE TRIGGER reject_reserve BEFORE INSERT ON death_mission_ledger BEGIN SELECT RAISE(ABORT, 'no'); END;"
        ),
        immediate=True,
    )
    with pytest.raises(sqlite3.IntegrityError):
        await mutate(service, preview.launch_token, armed, "commit")
    assert (
        await service.get_death_mission(preview.launch_token, now=NOW)
    ).status == "armed"
    assert await counts(service) == {}
    assert sum(a.amount for a in await service.get_agents(1)) == 6


@pytest.mark.asyncio
async def test_send_game_failure_publishes_same_two_mode_fallback(service):
    created = (
        await service.manual_spawn(CHAT - 1, event_type="death_operation", now=NOW)
    ).event
    bot = SimpleNamespace(
        send_game=AsyncMock(side_effect=RuntimeError("offline")),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=200)),
    )
    context = SimpleNamespace(
        bot=bot,
        bot_data={
            "spy_game": service,
            "spy_webapp": SimpleNamespace(
                game_enabled=True, settings=SimpleNamespace(game_short_name="spies")
            ),
        },
    )
    assert await spy_handlers.publish_spy_event(context, created) == 200
    assert "All-in" in bot.send_message.call_args.kwargs["text"]
    assert "Tier 4" in bot.send_message.call_args.kwargs["text"]
    markup = bot.send_message.call_args.kwargs["reply_markup"]
    assert (
        markup.inline_keyboard[0][0].callback_data
        == f"spy:deathmenu:{created.event_id}"
    )


@pytest.mark.asyncio
async def test_telegram_entry_confirmation_and_all_in_result(service):
    event_id = await service.database.read(
        lambda c: c.execute("SELECT id FROM game_events").fetchone()[0]
    )
    query = SimpleNamespace(
        data=f"spy:deathmenu:{event_id}",
        id="open",
        message=SimpleNamespace(message_id=100),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(
            id=1, username="agent1", full_name="PRIVATE NAME"
        ),
        effective_chat=SimpleNamespace(id=CHAT),
    )
    bot = SimpleNamespace(edit_message_text=AsyncMock())
    context = SimpleNamespace(bot=bot, bot_data={"spy_game": service})
    await spy_handlers.spy_callback(update, context)
    for key in ("choose_all_in", "confirm"):
        markup = query.edit_message_text.call_args.kwargs["reply_markup"]
        query.data = markup.inline_keyboard[0][0].callback_data
        query.id = key
        await spy_handlers.spy_callback(update, context)
    assert await counts(service) == {"reserve": 1, "settle": 1}
    assert "@agent1" in bot.edit_message_text.call_args.kwargs["text"]
    assert "PRIVATE NAME" not in bot.edit_message_text.call_args.kwargs["text"]
