import asyncio
from collections import Counter
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction
from itertools import product
import json

import pytest
import pytest_asyncio
from aiohttp import web

from spy_game.service import SpyGameService
from spy_game.slots import (
    REEL,
    RULES_VERSION,
    SYMBOLS,
    multiplier,
    return_to_player,
    rules_payload,
)
from spy_game.webapp import SpyWebAppServer
from test_spy_game import (
    CHAT_ID,
    NOW,
    SequenceRandom,
    grant_agents,
    initialized_service,
    spawn_event,
)
from test_spy_webapp import BOT_TOKEN, make_init_data, request, web_settings


@pytest_asyncio.fixture
async def service(tmp_path):
    game = await initialized_service(tmp_path)
    await grant_agents(game, 1, {"informant": 10, "operative": 2})
    try:
        yield game
    finally:
        await game.close()


async def spin(service, operation_id="spin-a", stake=1, **kwargs):
    return await service.spin_slots(
        operation_id=operation_id,
        stake=stake,
        user_id=kwargs.pop("user_id", 1),
        chat_id=kwargs.pop("chat_id", CHAT_ID),
        now=kwargs.pop("now", NOW),
        **kwargs,
    )


async def balance(service, user_id=1):
    agents = await service.get_agents(user_id)
    return next(
        (agent.amount for agent in agents if agent.agent_type == "informant"), 0
    )


def test_exact_payout_distribution_and_return():
    assert len(REEL) == 24
    assert REEL[:6] == tuple(symbol[0] for symbol in SYMBOLS)
    assert Counter(REEL) == {key: weight for key, _, _, _, weight in SYMBOLS}
    outcomes = [multiplier(stops) for stops in product(REEL, repeat=3)]
    total = len(outcomes)
    assert total == 24**3
    wins = Counter(value for value in outcomes if value > 1)
    assert wins == {2: 12**3, 6: 5**3, 15: 3**3, 50: 2**3, 100: 1, 150: 1}
    # Hit frequency above one in eight; fewer than a third of spins lose the stake.
    assert Fraction(sum(wins.values()), total) == Fraction(1890, 13824)
    assert Fraction(outcomes.count(0), total) == Fraction(4356, 13824)
    assert Fraction(sum(outcomes), total) == return_to_player() == Fraction(12839, 13824)
    payload = rules_payload()
    assert payload["version"] == RULES_VERSION == "v2"
    assert payload["rtp_percent"] == 92.87
    assert payload["reel_stops"] == 24
    assert [s["chance_percent"] for s in payload["symbols"]] == [
        50.0, 20.83, 12.5, 8.33, 4.17, 4.17,
    ]
    assert sum(s["weight"] for s in payload["symbols"]) == 24


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reels,symbols,factor",
    [
        ((0, 1, 2), ["file", "key", "radio"], 0),
        ((0, 0, 2), ["file", "file", "radio"], 1),
        ((0, 0, 0), ["file", "file", "file"], 2),
        # Stops 6–16 are the extra copies of the common symbol on the strip.
        ((6, 16, 0), ["file", "file", "file"], 2),
        ((23, 3, 23), ["case", "case", "case"], 50),
        ((5, 5, 5), ["spy", "spy", "spy"], 150),
    ],
)
@pytest.mark.parametrize("stake", [1, 3, 5])
async def test_atomic_stake_payout_history_without_event(
    service, reels, symbols, factor, stake
):
    service.repository.context.rng = SequenceRandom(*reels)
    result = await spin(service, stake=stake)
    assert result["ok"]
    assert result["spin"]["symbols"] == symbols
    assert result["spin"]["rules_version"] == "v2"
    assert result["spin"]["payout"] == stake * factor
    assert result["spin"]["net"] == stake * (factor - 1)
    assert (
        result["spin"]["balance_after"]
        == await balance(service)
        == 10 - stake + stake * factor
    )
    assert (await service.slot_state(1, CHAT_ID))["history"] == [result["spin"]]
    assert (
        await service.database.read(
            lambda c: c.execute("SELECT count(*) FROM game_events").fetchone()[0]
        )
        == 0
    )
    assert (
        next(
            a.amount for a in await service.get_agents(1) if a.agent_type == "operative"
        )
        == 2
    )


@pytest.mark.asyncio
async def test_spin_available_during_active_event(service):
    active = await spawn_event(service, "recruitment")
    assert (await spin(service))["ok"]
    assert (
        await service.get_chat_status(CHAT_ID)
    ).active_event_id == active.event.event_id


@pytest.mark.asyncio
async def test_retry_and_restart_return_exact_result_without_rng_or_debit(service):
    service.repository.context.rng = SequenceRandom(0, 1, 2)
    first = await spin(service, stake=5)
    assert await spin(service, stake=5) == first
    restarted = SpyGameService(service.settings, rng=SequenceRandom())
    await restarted.initialize(now=NOW + timedelta(seconds=1))
    try:
        assert await spin(restarted, stake=5) == first
        assert await balance(restarted) == 5
        assert (await restarted.slot_state(1, CHAT_ID))["history"] == [first["spin"]]
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_duplicate_requests_across_connections_settle_once(service):
    service.repository.context.rng = SequenceRandom(0, 1, 2)
    second = SpyGameService(service.settings, rng=SequenceRandom(0, 1, 2))
    await second.initialize(now=NOW)
    try:
        a, b = await asyncio.gather(spin(service, stake=5), spin(second, stake=5))
        assert a == b and a["ok"]
        assert await balance(service) == 5
        assert len((await service.slot_state(1, CHAT_ID))["history"]) == 1
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_concurrent_distinct_spins_and_cross_chat_cooldown(service):
    a, b = await asyncio.gather(spin(service, "a"), spin(service, "b"))
    assert sorted([a["status"], b["status"]]) == ["cooldown", "success"]
    other_chat = CHAT_ID - 1
    expanded = replace(
        service.settings, allowed_chat_ids=frozenset({CHAT_ID, other_chat})
    )
    service.context.settings = expanded
    service.repository.context.settings = expanded
    await service.enable_chat(other_chat, now=NOW)
    assert (await spin(service, "new", chat_id=other_chat))["status"] == "cooldown"
    assert (await spin(service, "a", stake=3))["status"] in {"conflict", "cooldown"}
    assert (await spin(service, "next", now=NOW + timedelta(seconds=2)))["ok"]


@pytest.mark.asyncio
async def test_operation_ids_are_bound_to_user_stake_and_chat(service):
    result = await spin(service)
    assert (await spin(service, stake=3))["status"] == "conflict"
    await grant_agents(service, 2, {"informant": 5})
    other = await spin(service, user_id=2)
    assert other["ok"]
    assert other["spin"]["balance_after"] != result["spin"]["balance_after"]
    assert len((await service.slot_state(1, CHAT_ID))["history"]) == 1
    assert len((await service.slot_state(2, CHAT_ID))["history"]) == 1
    other_chat = CHAT_ID - 1
    expanded = replace(
        service.settings, allowed_chat_ids=frozenset({CHAT_ID, other_chat})
    )
    service.context.settings = expanded
    await service.enable_chat(other_chat, now=NOW)
    assert (await spin(service, chat_id=other_chat))["status"] == "conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize("stake", [True, 1.0, "1", None, [], 0, -1, 2, 100000000])
async def test_invalid_stakes_do_not_touch_balance(service, stake):
    with pytest.raises(ValueError):
        await spin(service, stake=stake)
    assert await balance(service) == 10


@pytest.mark.asyncio
@pytest.mark.parametrize("operation_id", [None, [], "", "a" * 129, "a b", "../a"])
async def test_invalid_ids_do_not_touch_balance(service, operation_id):
    with pytest.raises(ValueError):
        await spin(service, operation_id=operation_id)
    assert await balance(service) == 10


@pytest.mark.asyncio
async def test_insufficient_agents_disabled_chat_and_global_flag(service):
    await grant_agents(service, 1, {"informant": 0})
    assert (await spin(service))["status"] == "insufficient_agents"
    assert (await service.slot_state(1, CHAT_ID))["history"] == []
    await service.disable_chat(CHAT_ID, now=NOW)
    assert (await spin(service))["status"] == "disabled"
    await service.enable_chat(CHAT_ID, now=NOW)
    assert (await spin(service, chat_id=CHAT_ID - 1))["status"] == "disabled"
    service.context.settings = replace(service.settings, enabled=False)
    assert (await spin(service))["status"] == "disabled"


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_debit_payout_and_history(service):
    original = service.database.before_commit

    def fail(_connection):
        raise RuntimeError("failed commit")

    service.database.before_commit = fail
    try:
        with pytest.raises(RuntimeError, match="failed commit"):
            await spin(service, stake=5)
    finally:
        service.database.before_commit = original
    assert await balance(service) == 10
    assert (await service.slot_state(1, CHAT_ID))["history"] == []
    assert (await spin(service, stake=5))["ok"]


@pytest.mark.asyncio
async def test_history_is_bounded_but_old_spin_remains_replayable(service):
    first = None
    for index in range(12):
        service.repository.context.rng = SequenceRandom(0, 0, 1)
        result = await spin(service, str(index), now=NOW + timedelta(seconds=index * 2))
        first = first or result
    history = (await service.slot_state(1, CHAT_ID))["history"]
    assert len(history) == 10
    assert [entry["operation_id"] for entry in history] == [
        str(i) for i in range(11, 1, -1)
    ]
    assert await spin(service, "0") == first
    assert await balance(service) == 10


@pytest.mark.asyncio
async def test_api_auth_validation_ownership_and_permanent_state(service):
    await service.touch_member_now(CHAT_ID, 1, now=NOW)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    headers = {
        "X-Telegram-Init-Data": make_init_data(
            user_id=1, start_param=server.signer.issue(CHAT_ID)
        )
    }
    body = {
        "operation_id": "web-spin",
        "stake": 1,
        "user_id": 999,
        "symbols": ["spy"] * 3,
        "payout": 999999,
    }
    service.repository.context.rng = SequenceRandom(0, 1, 2)
    response = await server.slot_spin(request(headers, body))
    result = json.loads(response.text)
    assert result["ok"] and result["spin"]["payout"] == 0
    assert await balance(service) == 9
    assert await balance(service, 999) == 0
    state = json.loads((await server.state(request(headers))).text)
    assert not state["context"]["active_event"]
    assert state["slots"]["history"] == [result["spin"]]
    assert state["slots"]["stakes"] == [1, 3, 5]
    with pytest.raises(web.HTTPBadRequest):
        await server.slot_spin(request(headers, {"operation_id": "bad", "stake": True}))
    with pytest.raises(web.HTTPUnauthorized):
        await server.slot_spin(request({}, body))
    # A user without membership in any enabled chat has no group context.
    with pytest.raises(web.HTTPForbidden):
        await server.slot_spin(
            request({"X-Telegram-Init-Data": make_init_data(user_id=2)}, body)
        )
    await service.disable_chat(CHAT_ID, now=NOW)
    with pytest.raises(web.HTTPForbidden):
        await server.slot_spin(request(headers, body))
    assert await balance(service) == 9
