"""Achievement invariants across real transactions, history replay and adapters."""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiohttp import web

from handlers.spy.achievements import archive_page, handle_archive
from spy_game.models import Reward
from spy_game.service import SpyGameService
from spy_game.settings import AGENT_TYPES, SpySettings
from spy_game.webapp import SpyWebAppServer
from test_spy_webapp import BOT_TOKEN, make_init_data, request, web_settings

NOW = datetime(2026, 9, 7, tzinfo=timezone.utc)
CHAT = -1234


def config(path):
    return SpySettings(
        mode="dev",
        enabled=True,
        database_path=path / "ach.sqlite3",
        allowed_chat_ids=frozenset({CHAT}),
        allow_manual_spawn=True,
    )


@pytest_asyncio.fixture
async def service(tmp_path):
    game = SpyGameService(config(tmp_path))
    await game.initialize(now=NOW)
    await game.enable_chat(CHAT, now=NOW)
    for uid in (1, 2):
        await game.get_profile(
            user_id=uid, username=f"agent{uid}", display_name="Private", now=NOW
        )
    try:
        yield game
    finally:
        await game.close()


async def grant(game, amount, agent="informant", uid=1):
    await game.database.transaction(
        lambda c: game.repository.economy.add_reward(c, uid, Reward(agent, amount)),
        immediate=True,
    )


async def archive(game, uid=1):
    return {a["id"]: a for a in (await game.get_achievements(uid))["entries"]}


def event_fact(c, key, event, outcome, *, uid=1, meta=None, reward=None, mission=None):
    c.execute(
        "INSERT INTO game_events(id,chat_id,event_type,status,payload_json,created_at,expires_at) "
        "VALUES(?,?,?,'resolved','{}',?,?)",
        (key, CHAT, event, NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat()),
    )
    if mission:
        c.execute(
            "INSERT INTO death_mission_runs(id,event_id,user_id,token_hash,status,mode,tactic,"
            "stake_json,rules_json,state_json,result_json,committed_at,expires_at) "
            "VALUES(?,?,?,?,?,'mission',?,'{}','{}',?,?,?,?)",
            (
                key,
                key,
                uid,
                key,
                outcome,
                mission.get("tactic", "balanced"),
                json.dumps(mission.get("state", {})),
                json.dumps(mission.get("result", {})),
                NOW.isoformat(),
                (NOW + timedelta(hours=1)).isoformat(),
            ),
        )
    c.execute(
        "INSERT INTO event_history(idempotency_key,event_id,chat_id,user_id,event_type,outcome,"
        "reward_type,reward_id,reward_amount,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            key,
            key,
            CHAT,
            uid,
            event,
            outcome,
            *(reward or (None, None, None)),
            json.dumps(meta or {}),
            NOW.isoformat(),
        ),
    )


@pytest.mark.asyncio
async def test_peaks_collection_and_title_survive_spending_and_restart(service):
    await grant(service, 100)
    for agent in AGENT_TYPES:
        if agent != "informant":
            await grant(service, 1, agent)
    a = await archive(service)
    assert all(
        a[key]["unlocked"]
        for key in ("network100", "director", "ghost", "collection", "fullstaff")
    )
    assert a["network500"]["progress"] == 111
    assert await service.select_achievement_title(1, "director")
    assert not await service.select_achievement_title(2, "director")
    assert not await service.select_achievement_title(1, "network500")
    await service.database.transaction(
        lambda c: c.execute("DELETE FROM user_agents WHERE user_id=1"), immediate=True
    )
    first = await service.get_achievements(1)
    await service.close()
    restarted = SpyGameService(service.settings)
    await restarted.initialize(now=NOW)
    try:
        assert await restarted.get_achievements(1) == first
        assert first["title"] == "Серый кардинал"
        assert await restarted.select_achievement_title(1, None)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_collection_does_not_require_simultaneous_ownership(service):
    for agent in AGENT_TYPES:
        await grant(service, 1, agent)
        await service.database.transaction(
            lambda c: c.execute("DELETE FROM user_agents WHERE user_id=1"),
            immediate=True,
        )
    a = await archive(service)
    assert a["collection"]["unlocked"]
    assert not a["fullstaff"]["unlocked"]
    assert a["fullstaff"]["progress"] == 1


@pytest.mark.asyncio
async def test_rollback_including_projection_and_concurrent_reward_replay(service):
    original = service.database.before_commit

    def fail(c):
        original(c)
        raise RuntimeError("after achievement unlock")

    service.database.before_commit = fail
    with pytest.raises(RuntimeError, match="after achievement"):
        await grant(service, 100)
    service.database.before_commit = original
    assert not (await archive(service))["first"]["unlocked"]
    assert await service.get_agents(1) == ()
    spawned = await service.manual_spawn(CHAT, now=NOW)

    async def claim():
        return await service.claim_event(
            event_id=spawned.event.event_id,
            action="claim",
            chat_id=CHAT,
            user_id=1,
            username="agent1",
            display_name="Private",
            now=NOW,
        )

    await asyncio.gather(*(claim() for _ in range(6)))
    assert (await archive(service))["first"]["unlocked"]
    assert (await service.get_agents(1))[0].amount == 1
    assert (
        await service.database.read(
            lambda c: c.execute(
                "SELECT COUNT(*) FROM user_achievements WHERE user_id=1 AND achievement_id='first'"
            ).fetchone()[0]
        )
        == 1
    )


@pytest.mark.asyncio
async def test_progress_before_reset_and_empty_office_are_permanent(service):
    def progress(c):
        c.execute("UPDATE users SET reputation=10 WHERE user_id=1")
        c.execute("UPDATE users SET reputation=0,agency_level=5 WHERE user_id=1")

    await service.database.transaction(progress, immediate=True)
    a = await archive(service)
    assert all(
        a[key]["unlocked"]
        for key in (
            "rep3",
            "rep5",
            "rep10",
            "agency1",
            "agency3",
            "agencymax",
            "office",
        )
    )
    await service.mark_achievements_seen(1, ["rep3"])
    a = await archive(service)
    assert not a["rep3"]["is_new"] and a["rep5"]["is_new"]


@pytest.mark.asyncio
async def test_personal_wins_tactics_extraction_and_all_in_are_distinct(service):
    def facts(c):
        for i in range(10):
            event_fact(c, f"all{i}", "death_operation", "won")
        for i, tactic in enumerate(("balanced", "stealth", "assault")):
            event_fact(
                c,
                f"personal{i}",
                "death_operation",
                "won",
                mission={"tactic": tactic, "state": {"hp": 1, "survived_raid": True}},
            )
        event_fact(
            c,
            "evac",
            "death_operation",
            "extracted",
            mission={"result": {"returned": [{"id": "informant", "amount": 2}]}},
        )
        event_fact(
            c,
            "timeout",
            "death_operation",
            "timed_out",
            mission={"result": {"returned": [{"id": "informant", "amount": 2}]}},
        )

    await service.database.transaction(facts, immediate=True)
    a = await archive(service)
    assert all(
        a[key]["unlocked"]
        for key in ("death10", "personal1", "lastbreath", "raid", "tactics", "extract")
    )
    assert a["personal10"]["progress"] == 3
    # Reading/restarting projections must never count those journals again.
    assert await archive(service) == a


@pytest.mark.asyncio
async def test_timeout_and_empty_extraction_do_not_unlock_plan_b(service):
    def facts(c):
        event_fact(
            c,
            "timeout",
            "death_operation",
            "timed_out",
            mission={"result": {"returned": [{"id": "informant", "amount": 2}]}},
        )
        event_fact(
            c,
            "empty-extract",
            "death_operation",
            "extracted",
            mission={"result": {"returned": []}},
        )

    await service.database.transaction(facts, immediate=True)
    a = await archive(service)
    assert not a["extract"]["unlocked"]
    assert not a["death1"]["unlocked"]


@pytest.mark.asyncio
async def test_recovery_requires_actual_empty_loss_and_later_agents(service):
    await grant(service, 100)
    await service.database.transaction(
        lambda c: event_fact(c, "nonempty", "death_operation", "lost"), immediate=True
    )
    await grant(service, 1)
    assert not (await archive(service))["comeback"]["unlocked"]

    def lose(c):
        c.execute("UPDATE user_agents SET amount=0 WHERE user_id=1")
        event_fact(c, "emptyloss", "death_operation", "lost")

    await service.database.transaction(lose, immediate=True)
    assert not (await archive(service))["comeback"]["unlocked"]
    await grant(service, 100)
    assert (await archive(service))["comeback"]["unlocked"]


@pytest.mark.asyncio
async def test_event_counters_count_success_and_completed_cooperation_only(service):
    def facts(c):
        for i in range(10):
            event_fact(c, f"int{i}", "intercept", "correct" if i % 2 else "won")
            event_fact(c, f"code{i}", "dead_drop", "won", reward=("empty", None, 0))
            event_fact(c, f"mole{i}", "find_mole", "won")
            event_fact(c, f"coop{i}", "cooperative_operation", "rewarded")
        event_fact(c, "wrong", "intercept", "incorrect")
        event_fact(c, "searched", "dead_drop", "searched")
        event_fact(c, "contribution", "cooperative_operation", "contributed")
        event_fact(c, "npc", "npc", "exchanged", meta={"reward_multiplier": 2})

    await service.database.transaction(facts, immediate=True)
    a = await archive(service)
    assert all(
        a[key]["unlocked"]
        for key in ("intercept", "codes", "mole10", "coop", "empty", "raredeal")
    )
    metrics = await service.database.read(
        lambda c: dict(
            c.execute("SELECT metric,value FROM achievement_metrics WHERE user_id=1")
        )
    )
    assert [metrics[k] for k in ("intercept", "codes", "moles", "coop")] == [10] * 4


@pytest.mark.asyncio
async def test_chase_roles_require_rewards_and_solo_requires_same_event(service):
    await service.database.transaction(
        lambda c: event_fact(c, "start", "chase", "started"), immediate=True
    )
    assert not (await archive(service))["chase"]["unlocked"]
    for role in ("starter", "interceptor"):
        await service.database.transaction(
            lambda c, role=role: event_fact(
                c, role, "chase", "rewarded", meta={"role": role}
            ),
            immediate=True,
        )
    a = await archive(service)
    assert a["chase"]["unlocked"] and not a["solo"]["unlocked"]
    spawned = await service.manual_spawn(CHAT, event_type="chase", now=NOW)
    for _ in range(2):
        await service.advance_chase(
            event_id=spawned.event.event_id,
            chat_id=CHAT,
            user_id=1,
            username="agent1",
            display_name="Private",
            now=NOW,
        )
    assert (await archive(service))["solo"]["unlocked"]


@pytest.mark.asyncio
async def test_contacts_and_unique_duel_opponents(service):
    def facts(c):
        for index, npc in enumerate(
            (
                "handler",
                "recruiter",
                "operations_chief",
                "counterintelligence",
                "handler",
            )
        ):
            c.execute(
                "INSERT INTO economy_history(idempotency_key,user_id,action,recipe_id,metadata_json,created_at) VALUES(?,1,'exchange','recipe',?,?)",
                (
                    f"contact{index}",
                    json.dumps({"source": "operational_center", "npc_id": npc}),
                    NOW.isoformat(),
                ),
            )
        for index, (opponent, outcome) in enumerate(
            (
                (2, "moves"),
                (2, "moves"),
                (3, "forfeit"),
                (4, "move_timeout"),
                (5, "moves"),
                (6, "moves"),
                (7, "moves"),
                (8, "moves"),
            )
        ):
            key = f"duel{index}"
            c.execute(
                "INSERT INTO spy_duels(id,chat_id,challenger_user_id,opponent_username,agent_type,stake_amount,status,tie_breaker_role,scenario_json,created_at,expires_at) VALUES(?,?,1,'other','informant',1,'resolved','challenger','{}',?,?)",
                (key, CHAT, NOW.isoformat(), (NOW + timedelta(hours=1)).isoformat()),
            )
            c.execute(
                "INSERT INTO spy_duel_history(idempotency_key,duel_id,chat_id,challenger_user_id,opponent_user_id,winner_user_id,agent_type,stake_amount,pot_amount,outcome,created_at) VALUES(?,?,?,1,?,1,'informant',1,2,?,?)",
                (key, key, CHAT, opponent, outcome, NOW.isoformat()),
            )

    await service.database.transaction(facts, immediate=True)
    a = await archive(service)
    assert a["contacts"]["unlocked"] and a["duels"]["unlocked"]
    assert (
        await service.database.read(
            lambda c: c.execute(
                "SELECT value FROM achievement_metrics WHERE user_id=1 AND metric='opponents'"
            ).fetchone()[0]
        )
        == 5
    )


@pytest.mark.asyncio
async def test_upgrade_replays_retained_history_once_without_inventing_peaks(tmp_path):
    settings = config(tmp_path)
    with sqlite3.connect(settings.database_path) as c:
        c.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT)"
        )
        for path in sorted(
            (Path(__file__).parents[1] / "spy_game/migrations").glob("*.sql")
        ):
            version = int(path.name[:3])
            if version >= 14:
                break
            c.executescript(path.read_text())
            c.execute(
                "INSERT INTO schema_migrations VALUES(?,?)", (version, NOW.isoformat())
            )
        c.execute(
            "INSERT INTO users(user_id,created_at,updated_at) VALUES(1,?,?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        c.execute(
            "INSERT INTO chat_state(chat_id,activity_updated_at,updated_at) VALUES(?,?,?)",
            (CHAT, NOW.isoformat(), NOW.isoformat()),
        )
        event_fact(
            c,
            "old-director",
            "npc",
            "exchanged",
            reward=("agent", "intelligence_director", 1),
        )
        for i in range(10):
            event_fact(c, f"old-win{i}", "death_operation", "won")
        c.execute(
            "INSERT INTO economy_history(idempotency_key,user_id,action,recipe_id,metadata_json,created_at) VALUES('rep',1,'prestige','reputation','{\"to\":10}',?)",
            (NOW.isoformat(),),
        )
    game = SpyGameService(settings)
    await game.initialize(now=NOW)
    first = await game.get_achievements(1)
    a = await archive(game)
    assert all(a[k]["unlocked"] for k in ("first", "director", "death10", "rep10"))
    assert not a["network100"]["unlocked"]
    assert not a["office"]["unlocked"]
    await game.close()
    game = SpyGameService(settings)
    await game.initialize(now=NOW)
    try:
        assert await game.get_achievements(1) == first
    finally:
        await game.close()


@pytest.mark.asyncio
async def test_http_identity_title_validation_secrets_and_seen_scope(service):
    await grant(service, 1, "intelligence_director")
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    headers = {"X-Telegram-Init-Data": make_init_data(user_id=2)}
    # A forged body user_id cannot select or acknowledge another player's medal.
    response = await server.achievement_title(
        request(headers, {"user_id": 1, "achievement_id": "director"})
    )
    assert not json.loads(response.text)["ok"]
    await server.achievements_seen(
        request(headers, {"user_id": 1, "achievement_ids": ["director"]})
    )
    assert (await archive(service))["director"]["is_new"]
    with pytest.raises(web.HTTPUnauthorized):
        await server.achievement_title(request({}, {"achievement_id": "director"}))
    for payload in ({}, {"achievement_id": 1}, {"achievement_id": []}):
        with pytest.raises(web.HTTPBadRequest):
            await server.achievement_title(request(headers, payload))
    good = {"X-Telegram-Init-Data": make_init_data(user_id=1)}
    assert json.loads(
        (
            await server.achievement_title(
                request(good, {"achievement_id": "director"})
            )
        ).text
    )["ok"]
    data = json.loads((await server.state(request(good))).text)
    assert data["achievements"]["title"] == "Серый кардинал"
    secret = next(a for a in data["achievements"]["entries"] if a["id"] == "comeback")
    assert (
        secret["name"] == "Засекречено"
        and secret["target"] is None
        and secret["note"] is None
    )


@pytest.mark.asyncio
async def test_telegram_pages_are_bounded_and_owner_checked(service):
    await grant(service, 100)
    data = await service.get_achievements(1)
    for page in range(6):
        text, keyboard, entries = archive_page(data, 1, page)
        assert len(text) < 4096 and len(entries) <= 6
        assert all(
            len(b.callback_data.encode()) <= 64
            for row in keyboard.inline_keyboard
            for b in row
        )
    query = SimpleNamespace(answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=2),
        effective_chat=SimpleNamespace(id=CHAT),
    )
    context = SimpleNamespace(bot_data={"spy_game": service})
    await handle_archive(update, context, "title", "1_network100")
    assert query.answer.await_args.kwargs["show_alert"]
    assert (await service.get_achievements(1))["title_id"] is None
