"""Public HTTP load bounds and revocation of saved HTML5 sessions."""
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from spy_game.models import (
    DeadDropGameRun,
    DeadDropGameStatus,
    InterceptGameRun,
    InterceptGameStatus,
)
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings
from spy_game.webapp import SpyWebAppServer, SpyWebAppSettings, _RateLimiter
from time_utils import moscow_now


def request(token, payload=None, *, source="127.0.0.1"):
    async def body():
        return payload

    return SimpleNamespace(
        headers={"X-Spy-Game-Token": token, "X-Forwarded-For": source},
        remote=source,
        json=body,
        match_info={"action": "commit"},
    )


def test_rate_limiter_expires_inactive_subjects_without_resetting_live_limits():
    limiter = _RateLimiter(2, max_subjects=2)
    assert limiter.allow("a", now=0)
    assert limiter.allow("b", now=1)
    assert limiter.allow("a", now=2)
    assert not limiter.allow("c", now=3)
    assert not limiter.allow("a", now=3)
    assert set(limiter._requests) == {"a", "b"}
    assert limiter.allow("c", now=62)
    assert list(limiter._requests) == ["c"]


def test_rate_limiter_bounds_distinct_subjects():
    limiter = _RateLimiter(2)
    accepted = sum(limiter.allow(f"token-{i}", now=0) for i in range(10_000))
    assert accepted == limiter.max_subjects == len(limiter._requests)
    assert limiter.allow("after-idle", now=3600)
    assert list(limiter._requests) == ["after-idle"]


@pytest.mark.asyncio
async def test_invalid_game_tokens_share_a_bound_across_reads_and_mutations(monkeypatch):
    clock = [0]
    monkeypatch.setattr("spy_game.webapp_support.time.monotonic", lambda: clock[0])
    service = SimpleNamespace(
        get_html5_game=AsyncMock(return_value=(None, None)),
        get_intercept_game=AsyncMock(return_value=InterceptGameRun(InterceptGameStatus.NOT_FOUND)),
    )
    server = SpyWebAppServer(service, "TEST:TOKEN", SpyWebAppSettings(rate_limit_per_minute=2))
    routes = (
        server.game_state,
        server.game_finish,
        server.game_guess,
        server.game_mole_accuse,
        server.game_death_action,
    )
    budget = server.game_request_limiter.limit
    for i in range(budget + len(routes)):
        error = web.HTTPUnauthorized if i < budget else web.HTTPTooManyRequests
        with pytest.raises(error):
            await routes[i % len(routes)](request(f"invalid-{i}", source=f"192.0.2.{i}"))
    assert service.get_html5_game.await_count + service.get_intercept_game.await_count == budget
    assert len(server.game_request_limiter._requests) == 1
    assert len(server.game_rate_limiter._requests) + len(server.game_read_rate_limiter._requests) == budget
    clock[0] = 61
    with pytest.raises(web.HTTPUnauthorized):
        await server.game_state(request("after-idle"))
    assert len(server.game_read_rate_limiter._requests) == 1


@pytest.mark.asyncio
async def test_state_polling_does_not_consume_the_mutation_allowance():
    active = DeadDropGameRun(DeadDropGameStatus.READY)
    service = SimpleNamespace(
        get_html5_game=AsyncMock(return_value=("dead_drop", active)),
        guess_dead_drop_game=AsyncMock(return_value=active),
    )
    server = SpyWebAppServer(service, "TEST:TOKEN", SpyWebAppSettings(rate_limit_per_minute=2))
    for _ in range(2):
        assert (await server.game_state(request("valid-token"))).status == 200
    with pytest.raises(web.HTTPTooManyRequests):
        await server.game_state(request("valid-token"))
    response = await server.game_guess(request("valid-token", {"guess": [1, 2, 3]}))
    assert response.status == 200
    service.guess_dead_drop_game.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("game_type", ["intercept", "dead_drop", "find_mole"])
async def test_saved_html5_session_cannot_mutate_after_allowlist_revocation(tmp_path, game_type):
    chat_id, other_chat = -100123, -100456
    now = moscow_now()
    settings = SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "sessions.sqlite3",
        allowed_chat_ids=frozenset({chat_id, other_chat}),
        allow_manual_spawn=True,
        html5_mole_enabled=True,
        llm_mole_enabled=False,
    )
    service = SpyGameService(settings)
    await service.initialize(now=now)
    await service.enable_chat(chat_id, now=now)
    try:
        if game_type == "find_mole":
            await service.database.transaction(
                lambda c: c.execute(
                    "UPDATE chat_state SET story_arc='mole_hunt', story_stage=3 WHERE chat_id=?",
                    (chat_id,),
                ),
                immediate=True,
            )
        event = (await service.manual_spawn(chat_id, event_type=game_type, now=now)).event
        await service.attach_message(event.event_id, 77)
        run = await getattr(service, f"start_{game_type}_game")(
            chat_id=chat_id,
            message_id=77,
            user_id=42,
            username="bond",
            display_name=None,
            now=now,
        )
        assert run.status.value == "ready"
        if game_type == "intercept":
            payload = {"locks": list(run.targets)}
        elif game_type == "dead_drop":
            code = await service.database.read(
                lambda c: c.execute(
                    "SELECT code_json FROM dead_drop_game_runs WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()[0]
            )
            payload = {"guess": json.loads(code)}
        else:
            solution = await service.database.read(
                lambda c: c.execute(
                    "SELECT solution_suspect_id FROM find_mole_cases WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()[0]
            )
            payload = {
                "suspect_id": solution,
                "revision": run.revision,
                "idempotency_key": "revoked-session",
            }
    finally:
        await service.close()

    restricted = SpyGameService(replace(settings, allowed_chat_ids=frozenset({other_chat})))
    await restricted.initialize(now=now)
    server = SpyWebAppServer(restricted, "TEST:TOKEN", SpyWebAppSettings())
    tables = (
        "game_events",
        f"{game_type}_game_runs",
        "event_history",
        "economy_history",
        "user_agents",
        "user_items",
        "item_durability",
        "equipment_uses",
    )

    async def snapshot():
        return await restricted.database.read(
            lambda c: {table: [tuple(row) for row in c.execute(f"SELECT * FROM {table}")] for table in tables}
        )

    try:
        before = await snapshot()
        assert not restricted.chat_is_available(chat_id)
        read = await getattr(restricted, f"get_{game_type}_game")(run.launch_token, now=now)
        assert read.status.value == "disabled"
        with pytest.raises(web.HTTPServiceUnavailable):
            await server.game_state(request(run.launch_token))
        route = {
            "intercept": server.game_finish,
            "dead_drop": server.game_guess,
            "find_mole": server.game_mole_accuse,
        }[game_type]
        with pytest.raises(web.HTTPServiceUnavailable):
            await route(request(run.launch_token, payload))
        if game_type == "intercept":
            result = await restricted.finish_intercept_game(run.launch_token, tuple(payload["locks"]), now=now)
        elif game_type == "dead_drop":
            result = await restricted.guess_dead_drop_game(run.launch_token, tuple(payload["guess"]), now=now)
        else:
            result = await restricted.accuse_find_mole_game(run.launch_token, **payload, now=now)
        assert result.status.value == "disabled"
        assert await snapshot() == before
    finally:
        await restricted.close()
