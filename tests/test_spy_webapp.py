import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import pytest
from aiohttp import web

from spy_game.service import SpyGameService
from spy_game.settings import SpySettings
from spy_game.webapp import SpyWebAppServer, SpyWebAppSettings, _RateLimiter
from spy_game.webapp_auth import (
    LaunchContextSigner,
    WebAppAuthError,
    chat_handle,
    validate_init_data,
)


BOT_TOKEN = "123456:TEST_TOKEN"
CHAT_ID = -100123456
USER_ID = 42


def make_init_data(
    *,
    user_id=USER_ID,
    username="bond",
    display_name="James Bond",
    start_param=None,
    signature=None,
    auth_date=None,
):
    current = int(time.time()) if auth_date is None else auth_date
    user = {
        "id": user_id,
        "first_name": display_name.split()[0],
        "last_name": " ".join(display_name.split()[1:]),
        "username": username,
    }
    values = {
        "auth_date": str(current),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":")),
    }
    if start_param is not None:
        values["start_param"] = start_param
    if signature is not None:
        values["signature"] = signature
    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(
        secret,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(values)


def game_settings(tmp_path: Path, **overrides):
    values = {
        "mode": "dev",
        "enabled": True,
        "database_path": tmp_path / "spy-webapp.sqlite3",
        "allowed_chat_ids": frozenset({CHAT_ID}),
        "activity_user_debounce_seconds": 0,
        "allow_manual_spawn": True,
    }
    values.update(overrides)
    return SpySettings(
        llm_mole_enabled=False,
        **values,
    )


def web_settings(**overrides):
    values = {
        "enabled": True,
        "launch_url": "https://t.me/belmondo_test_bot/spy_center",
        "init_data_max_age_seconds": 300,
        "launch_context_ttl_seconds": 600,
        "rate_limit_per_minute": 60,
    }
    values.update(overrides)
    return SpyWebAppSettings(**values)


def request(headers=None, payload=None):
    async def json_body():
        return payload

    return SimpleNamespace(headers=headers or {}, json=json_body)


async def join(service, user_id=USER_ID, chat_id=CHAT_ID, title=None, now=None):
    """Record chat membership the way a group message or /spy would."""

    await service.touch_member_now(chat_id, user_id, title=title, now=now)


def test_telegram_init_data_validation_trusts_only_signed_fields():
    init_data = make_init_data(signature="telegram-ed25519-signature")
    identity = validate_init_data(
        init_data,
        BOT_TOKEN,
        max_age_seconds=300,
    )
    assert identity.user_id == USER_ID
    assert identity.username == "bond"
    assert identity.display_name == "James Bond"

    with pytest.raises(WebAppAuthError, match="hash"):
        validate_init_data(
            init_data.replace("bond", "villain"),
            BOT_TOKEN,
            max_age_seconds=300,
        )


def test_telegram_init_data_rejects_expired_and_duplicate_fields():
    now = 1_800_000_000
    expired = make_init_data(auth_date=now - 301)
    with pytest.raises(WebAppAuthError, match="expired"):
        validate_init_data(expired, BOT_TOKEN, max_age_seconds=300, now=now)

    duplicated = make_init_data() + "&auth_date=1"
    with pytest.raises(WebAppAuthError, match="duplicate"):
        validate_init_data(duplicated, BOT_TOKEN, max_age_seconds=300)


def test_launch_context_is_a_short_lived_chat_hint():
    signer = LaunchContextSigner(BOT_TOKEN, ttl_seconds=600)
    token = signer.issue(CHAT_ID, now=1000)

    assert len(token) <= 512
    assert set(token) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    # Any member of the chat may use the hint; it is not bound to the issuer.
    assert signer.verify(token, now=1200) == CHAT_ID
    with pytest.raises(WebAppAuthError, match="expired"):
        signer.verify(token, now=1601)
    with pytest.raises(WebAppAuthError):
        signer.verify(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_chat_handle_is_opaque_and_per_user():
    handle = chat_handle(BOT_TOKEN, USER_ID, CHAT_ID)
    assert len(handle) == 16 and set(handle) <= set("0123456789abcdef")
    assert str(CHAT_ID).lstrip("-") not in handle
    assert handle == chat_handle(BOT_TOKEN, USER_ID, CHAT_ID)
    assert handle != chat_handle(BOT_TOKEN, USER_ID + 1, CHAT_ID)
    assert handle != chat_handle(BOT_TOKEN, USER_ID, CHAT_ID - 1)
    assert handle != chat_handle("other:token", USER_ID, CHAT_ID)


def test_launch_url_keeps_only_an_opaque_signed_context(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "spy_game.webapp_auth.secrets.token_bytes",
        lambda size: b"x" * size,
    )
    service = SimpleNamespace(settings=game_settings(tmp_path))
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    url = server.launch_url(CHAT_ID)

    assert url.startswith("https://t.me/belmondo_test_bot/spy_center?startapp=")
    assert str(CHAT_ID) not in url
    assert server.launch_url(USER_ID) == "https://t.me/belmondo_test_bot/spy_center"


def test_webapp_settings_require_https_launch_url():
    with pytest.raises(ValueError, match="required"):
        SpyWebAppSettings(enabled=True)
    with pytest.raises(ValueError, match="HTTPS"):
        SpyWebAppSettings(enabled=True, launch_url="http://example.test/app")
    with pytest.raises(ValueError, match="Telegram direct link"):
        SpyWebAppSettings(enabled=True, launch_url="https://example.test/app")
    with pytest.raises(ValueError, match="public HTTPS"):
        SpyWebAppSettings(enabled=True, game_url="http://example.test/game")
    assert (
        SpyWebAppSettings(
            enabled=True,
            game_url="https://example.test/spy-app/game/",
        ).game_short_name
        == "spies"
    )


def test_rate_limiter_has_a_per_user_rolling_window():
    limiter = _RateLimiter(2)
    assert limiter.allow(USER_ID, now=100)
    assert limiter.allow(USER_ID, now=101)
    assert not limiter.allow(USER_ID, now=102)
    assert limiter.allow(USER_ID + 1, now=102)
    assert limiter.allow(USER_ID, now=161)


@pytest.mark.asyncio
async def test_webapp_state_and_equipment_use_same_service_and_database(tmp_path):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    await service.get_profile(
        user_id=USER_ID,
        username="old_username",
        display_name="Old Private Name",
    )
    await service.database.transaction(
        lambda connection: connection.execute(
            "INSERT INTO user_items(user_id, item_type, amount) VALUES (?, ?, ?)",
            (USER_ID, "radio", 1),
        ),
        immediate=True,
    )
    await join(service)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    # No /spy link at all: membership alone resolves the group context.
    headers = {"X-Telegram-Init-Data": make_init_data()}

    try:
        response = await server.state(request(headers))
        assert response.status == 200
        payload = json.loads(response.text)
        assert payload["profile"]["username"] == "@bond"
        assert "James Bond" not in json.dumps(payload)
        assert payload["context"]["can_mutate"] is True
        radio = next(
            item for item in payload["inventory"]["items"] if item["id"] == "radio"
        )
        assert radio["category"] == "equipment"
        assert radio["max_uses"] == 3 and radio["uses_remaining"] is None
        assert (
            radio["exchangeable_amount"] == 1 and "+1 осведомитель" in radio["effect"]
        )

        response = await server.equip(request(headers, {"item_type": "radio"}))
        assert response.status == 200
        assert json.loads(response.text) == {
            "ok": True,
            "status": "success",
            "slot": 1,
        }
        inventory = await service.get_inventory(USER_ID)
        assert [(item.slot, item.item_type) for item in inventory.equipped] == [
            (1, "radio")
        ]
        equipped_state = json.loads((await server.state(request(headers))).text)
        radio = next(
            item
            for item in equipped_state["inventory"]["items"]
            if item["id"] == "radio"
        )
        assert radio["uses_remaining"] == 3 and radio["exchangeable_amount"] == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_context_comes_from_membership_not_from_the_link(tmp_path):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    stranger_headers = {"X-Telegram-Init-Data": make_init_data()}
    token = server.signer.issue(CHAT_ID)
    forwarded_headers = {
        "X-Telegram-Init-Data": make_init_data(start_param=token),
    }
    other_headers = {
        "X-Telegram-Init-Data": make_init_data(
            user_id=USER_ID + 1,
            username="other",
            start_param=token,
        )
    }

    try:
        # A user never seen in any enabled chat stays read-only, link or not.
        payload = json.loads((await server.state(request(stranger_headers))).text)
        assert payload["context"] == {
            "chat_bound": False,
            "chats": [],
            "can_mutate": False,
            "network_enabled": False,
            "activity_score": None,
            "activity_profile": None,
            "active_event": False,
        }
        payload = json.loads((await server.state(request(forwarded_headers))).text)
        assert payload["context"]["can_mutate"] is False
        with pytest.raises(web.HTTPForbidden):
            await server.prestige(
                request(forwarded_headers, {"expected_reputation": 0})
            )

        # Another member pressing the first user's /spy button is fine.
        await join(service, user_id=USER_ID + 1)
        payload = json.loads((await server.state(request(other_headers))).text)
        assert payload["context"]["can_mutate"] is True

        # Expired or garbage hints are ignored rather than rejected.
        await join(service)
        for hint in ("not-a-token", server.signer.issue(CHAT_ID, now=0)):
            headers = {"X-Telegram-Init-Data": make_init_data(start_param=hint)}
            payload = json.loads((await server.state(request(headers))).text)
            assert payload["context"]["chat_bound"] is True
            assert payload["context"]["can_mutate"] is True

        # Disabling the chat removes it from the resolved memberships.
        await service.disable_chat(CHAT_ID)
        payload = json.loads((await server.state(request(forwarded_headers))).text)
        assert payload["context"]["chat_bound"] is False
        with pytest.raises(web.HTTPForbidden):
            await server.prestige(
                request(forwarded_headers, {"expected_reputation": 0})
            )
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_lists_member_chats_and_honours_selection(tmp_path):
    other_chat = CHAT_ID - 1
    service = SpyGameService(
        game_settings(tmp_path, allowed_chat_ids=frozenset({CHAT_ID, other_chat}))
    )
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    await service.enable_chat(other_chat)
    base = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)
    await join(service, chat_id=other_chat, title="Old Network", now=base)
    await join(
        service,
        chat_id=CHAT_ID,
        title="Fresh Network",
        now=base + timedelta(minutes=5),
    )
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    plain = {"X-Telegram-Init-Data": make_init_data()}
    old_handle = chat_handle(BOT_TOKEN, USER_ID, other_chat)
    fresh_handle = chat_handle(BOT_TOKEN, USER_ID, CHAT_ID)

    try:
        # Most recently seen chat is selected by default; IDs never leak.
        payload = json.loads((await server.state(request(plain))).text)
        assert payload["context"]["chats"] == [
            {"handle": fresh_handle, "title": "Fresh Network", "selected": True},
            {"handle": old_handle, "title": "Old Network", "selected": False},
        ]
        assert str(CHAT_ID) not in json.dumps(payload["context"])

        # An explicit selection wins over recency and over the /spy hint.
        hint = server.signer.issue(CHAT_ID)
        selected = {
            "X-Telegram-Init-Data": make_init_data(start_param=hint),
            "X-Spy-Chat": old_handle,
        }
        payload = json.loads((await server.state(request(selected))).text)
        assert [chat["selected"] for chat in payload["context"]["chats"]] == [
            False,
            True,
        ]

        # A hint without a stored selection wins over recency.
        hinted = {"X-Telegram-Init-Data": make_init_data(start_param=server.signer.issue(other_chat))}
        payload = json.loads((await server.state(request(hinted))).text)
        assert payload["context"]["chats"][1]["selected"] is True

        # Foreign or malformed handles fall back to the default.
        for bogus in ("0123456789abcdef", "<script>", chat_handle(BOT_TOKEN, 7, other_chat)):
            headers = {"X-Telegram-Init-Data": make_init_data(), "X-Spy-Chat": bogus}
            payload = json.loads((await server.state(request(headers))).text)
            assert payload["context"]["chats"][0]["selected"] is True

        # Mutations run in the selected chat.
        await service.get_profile(user_id=USER_ID, username="bond", display_name="J")
        await service.database.transaction(
            lambda connection: connection.execute(
                "INSERT INTO user_items(user_id, item_type, amount) VALUES (?, ?, ?)",
                (USER_ID, "radio", 1),
            ),
            immediate=True,
        )
        response = await server.equip(
            request(
                {"X-Telegram-Init-Data": make_init_data(), "X-Spy-Chat": old_handle},
                {"item_type": "radio"},
            )
        )
        assert json.loads(response.text)["ok"] is True
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_prestige_and_agency_keep_stale_checks_server_side(tmp_path):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    await service.get_profile(
        user_id=USER_ID,
        username="bond",
        display_name="James Bond",
    )
    await service.database.transaction(
        lambda connection: connection.executemany(
            "INSERT INTO user_agents(user_id, agent_type, amount) VALUES (?, ?, ?)",
            [
                (USER_ID, "operative", 1),
                (USER_ID, "observer", 1),
                (USER_ID, "courier", 1),
            ],
        ),
        immediate=True,
    )
    await join(service)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    launch_token = server.signer.issue(CHAT_ID)
    headers = {
        "X-Telegram-Init-Data": make_init_data(start_param=launch_token),
    }

    try:
        response = await server.prestige(request(headers, {"expected_reputation": 0}))
        assert json.loads(response.text)["status"] == "success"

        stale = await server.prestige(request(headers, {"expected_reputation": 0}))
        assert json.loads(stale.text) == {
            "ok": False,
            "status": "stale",
            "reputation": 1,
            "required": [],
        }

        await service.database.transaction(
            lambda connection: (
                connection.execute(
                    "UPDATE users SET reputation = 3 WHERE user_id = ?",
                    (USER_ID,),
                ),
                connection.executemany(
                    """
                    INSERT INTO user_agents(user_id, agent_type, amount)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, agent_type)
                    DO UPDATE SET amount = excluded.amount
                    """,
                    [
                        (USER_ID, "intelligence_director", 1),
                        (USER_ID, "resident", 2),
                        (USER_ID, "illegal_agent", 2),
                    ],
                ),
            ),
            immediate=True,
        )
        response = await server.agency(request(headers, {"expected_level": 0}))
        result = json.loads(response.text)
        assert result["ok"] is True
        assert result["status"] == "success"
        assert result["agency_level"] == 1
        profile = await service.get_profile(
            user_id=USER_ID,
            username="bond",
            display_name="James Bond",
        )
        assert (profile.agency_level, profile.reputation) == (1, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_exposes_all_npc_exchanges_as_permanent_contacts(
    tmp_path,
):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    await service.get_profile(
        user_id=USER_ID,
        username="bond",
        display_name="Private Bond",
    )
    await service.database.transaction(
        lambda connection: (
            connection.execute(
                "INSERT INTO user_agents(user_id, agent_type, amount) VALUES (?, ?, ?)",
                (USER_ID, "operative", 1),
            ),
            connection.execute(
                "INSERT INTO user_agents(user_id, agent_type, amount) VALUES (?, ?, ?)",
                (USER_ID, "informant", 30),
            ),
            connection.execute(
                "INSERT INTO user_items(user_id, item_type, amount) VALUES (?, ?, ?)",
                (USER_ID, "fake_passport", 1),
            ),
        ),
        immediate=True,
    )
    await join(service)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    launch_token = server.signer.issue(CHAT_ID)
    headers = {
        "X-Telegram-Init-Data": make_init_data(start_param=launch_token),
    }
    try:
        response = await server.state(request(headers))
        state = json.loads(response.text)
        assert len(state["contacts"]) == 14
        assert {contact["npc_id"] for contact in state["contacts"]} == {
            "handler",
            "recruiter",
            "operations_chief",
            "counterintelligence",
        }
        recruiter = next(
            contact
            for contact in state["contacts"]
            if contact["id"] == "recruiter_network"
        )
        assert recruiter["reward"] == {
            "type": "random",
            "id": None,
            "name": "Случайный агент Tier 2–4",
            "emoji": "🎲",
            "amount": 1,
        }
        handler = next(
            contact for contact in state["contacts"] if contact["id"] == "handler_tier2"
        )
        assert handler["npc_name"] == "Куратор"
        assert handler["reward"]["name"] == "Случайный агент Tier 2"
        chief = next(
            contact for contact in state["contacts"] if contact["id"] == "chief_illegal"
        )
        assert chief["reward"]["id"] == "illegal_agent"
        assert chief["agent_costs"][0]["id"] == "operative"
        assert chief["item_costs"][0]["id"] == "fake_passport"

        response = await server.contact_exchange(
            request(
                headers,
                {"recipe_id": "chief_illegal", "operation_id": "web-operation-1"},
            )
        )
        result = json.loads(response.text)
        assert result["ok"] is True
        assert result["status"] == "success"
        assert result["reward"]["id"] == "illegal_agent"

        duplicate = await server.contact_exchange(
            request(
                headers,
                {"recipe_id": "chief_illegal", "operation_id": "web-operation-1"},
            )
        )
        assert json.loads(duplicate.text) == result

        handler_exchange = await server.contact_exchange(
            request(
                headers,
                {"recipe_id": "handler_tier2", "operation_id": "web-operation-2"},
            )
        )
        handler_result = json.loads(handler_exchange.text)
        assert handler_result["status"] == "success"
        assert handler_result["reward"]["amount"] == 1

        recruiter_exchange = await server.contact_exchange(
            request(
                headers,
                {
                    "recipe_id": "recruiter_network",
                    "operation_id": "web-operation-3",
                },
            )
        )
        recruiter_result = json.loads(recruiter_exchange.text)
        assert recruiter_result["status"] == "success"
        assert recruiter_result["reward"]["amount"] == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_static_files_and_health_do_not_require_telegram_auth(tmp_path):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    try:
        index = await server.index(request())
        assert index.status == 200
        assert "Spy Clicker" in (server.ASSETS / "index.html").read_text()
        assert "contact-list" in (server.ASSETS / "index.html").read_text()
        assert '"contacts/exchange"' in (server.ASSETS / "app.js").read_text()
        assert '"contact-row"' in (server.ASSETS / "app.js").read_text()
        assert "@media (max-width: 480px)" in (server.ASSETS / "styles.css").read_text()
        assert "Content-Security-Policy" in index.headers

        health = await server.health(request())
        assert health.status == 200
        assert json.loads(health.text) == {
            "ok": True,
            "game_enabled": True,
            "html5_game_enabled": False,
            "html5_mole_enabled": False,
        }
        game_javascript = await server.game_javascript(request())
        assert game_javascript.headers["Cache-Control"] == "no-store"
        assert "game.js?v=4" in (server.ASSETS / "game.html").read_text()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_html5_game_api_uses_persisted_token_and_announces_once(tmp_path):
    rng = SimpleNamespace(randint=lambda start, end: start)
    service = SpyGameService(game_settings(tmp_path), rng=rng)
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    event = (await service.manual_spawn(CHAT_ID, event_type="intercept")).event
    await service.attach_message(event.event_id, 900)
    run = await service.start_intercept_game(
        chat_id=CHAT_ID,
        message_id=900,
        user_id=USER_ID,
        username="bond",
        display_name="Private Bond",
    )
    bot = SimpleNamespace(
        edit_message_reply_markup=AsyncMock(),
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
    )
    settings = web_settings(game_url="https://spy.example/spy-app/game/")
    server = SpyWebAppServer(service, BOT_TOKEN, settings, bot=bot)
    headers = {"X-Spy-Game-Token": run.launch_token}
    try:
        assert server.game_enabled is True
        assert server.game_launch_url(run.launch_token).startswith(
            "https://spy.example/spy-app/game/#run="
        )
        with pytest.raises(web.HTTPUnauthorized):
            await server.game_state(request())

        response = await server.game_state(request(headers))
        state = json.loads(response.text)
        assert state["status"] == "ready"
        assert state["targets"] == [15, 15, 15, 15, 15]

        response = await server.game_finish(
            request(headers, {"locks": state["targets"]})
        )
        result = json.loads(response.text)
        assert result["status"] == "won"
        assert result["score"] == 5000
        assert result["reward"]["id"] == "access_code"
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
        assert "@bond" in bot.send_message.await_args.kwargs["text"]
        assert "Private Bond" not in bot.send_message.await_args.kwargs["text"]

        await server.game_finish(request(headers, {"locks": state["targets"]}))
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None

        game = await server.game(request())
        assert game.status == 200
        assert "Перехват сигнала" in (server.ASSETS / "game.html").read_text(
            encoding="utf-8"
        )
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_html5_dead_drop_api_keeps_code_server_side_and_announces_once(tmp_path):
    rng = SimpleNamespace(randint=lambda start, end: start)
    service = SpyGameService(game_settings(tmp_path), rng=rng)
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    event = (await service.manual_spawn(CHAT_ID, event_type="dead_drop")).event
    await service.attach_message(event.event_id, 901)
    run = await service.start_dead_drop_game(
        chat_id=CHAT_ID,
        message_id=901,
        user_id=USER_ID,
        username="bond",
        display_name="Private Bond",
    )
    bot = SimpleNamespace(
        edit_message_reply_markup=AsyncMock(),
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
    )
    server = SpyWebAppServer(
        service,
        BOT_TOKEN,
        web_settings(game_url="https://spy.example/spy-app/game/"),
        bot=bot,
    )
    headers = {"X-Spy-Game-Token": run.launch_token}
    try:
        response = await server.game_state(request(headers))
        state = json.loads(response.text)
        assert state["game_type"] == "dead_drop"
        assert state["status"] == "ready"
        assert state["code_length"] == 3
        assert "attempts_allowed" not in state
        assert "code" not in state

        response = await server.game_guess(request(headers, {"guess": [1, 2, 3]}))
        feedback = json.loads(response.text)
        assert feedback["status"] == "ready"
        assert feedback["attempts"] == [
            {"digits": [1, 2, 3], "exact": 0, "misplaced": 0}
        ]

        response = await server.game_guess(request(headers, {"guess": [0, 0, 0]}))
        result = json.loads(response.text)
        assert result["status"] == "won"
        assert result["reward"]["id"] == "intel_file"
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
        assert "@bond" in bot.send_message.await_args.kwargs["text"]
        assert "Private Bond" not in bot.send_message.await_args.kwargs["text"]

        await server.game_guess(request(headers, {"guess": [0, 0, 0]}))
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_html5_find_mole_api_hides_solution_and_announces_once(tmp_path):
    rng = SimpleNamespace(randint=lambda start, end: start)
    service = SpyGameService(
        game_settings(tmp_path, html5_mole_enabled=True),
        rng=rng,
    )
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    event = (await service.manual_spawn(CHAT_ID, event_type="find_mole")).event
    await service.attach_message(event.event_id, 902)
    solution = await service.database.read(
        lambda connection: connection.execute(
            "SELECT solution_suspect_id FROM find_mole_cases WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()[0]
    )
    run = await service.start_find_mole_game(
        chat_id=CHAT_ID,
        message_id=902,
        user_id=USER_ID,
        username="bond",
        display_name="Private Bond",
    )
    bot = SimpleNamespace(
        edit_message_reply_markup=AsyncMock(),
        edit_message_text=AsyncMock(),
        send_message=AsyncMock(),
    )
    server = SpyWebAppServer(
        service,
        BOT_TOKEN,
        web_settings(game_url="https://spy.example/spy-app/game/"),
        bot=bot,
    )
    headers = {"X-Spy-Game-Token": run.launch_token}
    try:
        response = await server.game_state(request(headers))
        state = json.loads(response.text)
        assert state["game_type"] == "find_mole"
        assert state["status"] == "ready"
        assert len(state["suspects"]) == 4
        assert "solution" not in response.text

        response = await server.game_mole_accuse(
            request(
                headers,
                {
                    "suspect_id": solution,
                    "revision": state["revision"],
                    "idempotency_key": "api-mole-attempt-1",
                },
            )
        )
        result = json.loads(response.text)
        assert result["status"] == "won"
        assert [(reward["type"], reward["amount"]) for reward in result["rewards"]] == [
            ("item", 1),
            ("agent", 3),
        ]
        assert result["rewards"][0]["id"] == "fake_passport"
        assert result["rewards"][1]["id"] == "informant"
        assert "solution" not in response.text
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
        assert "@bond" in bot.send_message.await_args.kwargs["text"]
        assert "Private Bond" not in bot.send_message.await_args.kwargs["text"]

        await server.game_mole_accuse(
            request(
                headers,
                {
                    "suspect_id": solution,
                    "revision": state["revision"],
                    "idempotency_key": "api-mole-attempt-1",
                },
            )
        )
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        assert bot.edit_message_text.await_args.kwargs["reply_markup"] is None
    finally:
        await service.close()
