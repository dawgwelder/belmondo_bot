import hashlib
import hmac
import json
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from aiohttp import web

from spy_game.service import SpyGameService
from spy_game.settings import SpySettings
from spy_game.webapp import SpyWebAppServer, SpyWebAppSettings, _RateLimiter
from spy_game.webapp_auth import (
    LaunchContextSigner,
    WebAppAuthError,
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


def game_settings(tmp_path: Path):
    return SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "spy-webapp.sqlite3",
        allowed_chat_ids=frozenset({CHAT_ID}),
        activity_user_debounce_seconds=0,
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


def test_launch_context_is_short_lived_and_bound_to_user_and_chat():
    signer = LaunchContextSigner(BOT_TOKEN, ttl_seconds=600)
    token = signer.issue(CHAT_ID, USER_ID, now=1000)

    assert len(token) <= 512
    assert set(token) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert signer.verify(token, USER_ID, now=1200) == CHAT_ID
    with pytest.raises(WebAppAuthError, match="mismatched"):
        signer.verify(token, USER_ID + 1, now=1200)
    with pytest.raises(WebAppAuthError, match="expired"):
        signer.verify(token, USER_ID, now=1601)
    with pytest.raises(WebAppAuthError):
        signer.verify(token[:-1] + ("A" if token[-1] != "A" else "B"), USER_ID)


def test_launch_url_keeps_only_an_opaque_signed_context(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "spy_game.webapp_auth.secrets.token_bytes",
        lambda size: b"x" * size,
    )
    service = SimpleNamespace(settings=game_settings(tmp_path))
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    url = server.launch_url(CHAT_ID, USER_ID)

    assert url.startswith("https://t.me/belmondo_test_bot/spy_center?startapp=")
    assert str(CHAT_ID) not in url
    assert str(USER_ID) not in url
    assert (
        server.launch_url(USER_ID, USER_ID)
        == "https://t.me/belmondo_test_bot/spy_center"
    )


def test_webapp_settings_require_https_launch_url():
    with pytest.raises(ValueError, match="required"):
        SpyWebAppSettings(enabled=True)
    with pytest.raises(ValueError, match="HTTPS"):
        SpyWebAppSettings(enabled=True, launch_url="http://example.test/app")
    with pytest.raises(ValueError, match="Telegram direct link"):
        SpyWebAppSettings(enabled=True, launch_url="https://example.test/app")


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
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    launch_token = server.signer.issue(CHAT_ID, USER_ID)
    headers = {
        "X-Telegram-Init-Data": make_init_data(start_param=launch_token),
    }

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
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_webapp_profile_launch_is_read_only_and_group_token_is_user_bound(
    tmp_path,
):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    await service.enable_chat(CHAT_ID)
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    read_only_headers = {"X-Telegram-Init-Data": make_init_data()}
    stolen_token = server.signer.issue(CHAT_ID, USER_ID)
    stolen_headers = {
        "X-Telegram-Init-Data": make_init_data(
            user_id=USER_ID + 1,
            username="other",
            start_param=stolen_token,
        )
    }

    try:
        response = await server.state(request(read_only_headers))
        assert response.status == 200
        assert json.loads(response.text)["context"]["can_mutate"] is False

        with pytest.raises(web.HTTPForbidden):
            await server.prestige(
                request(read_only_headers, {"expected_reputation": 0})
            )

        with pytest.raises(web.HTTPUnauthorized):
            await server.state(request(stolen_headers))
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
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    launch_token = server.signer.issue(CHAT_ID, USER_ID)
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
async def test_webapp_static_files_and_health_do_not_require_telegram_auth(tmp_path):
    service = SpyGameService(game_settings(tmp_path))
    await service.initialize()
    server = SpyWebAppServer(service, BOT_TOKEN, web_settings())
    try:
        index = await server.index(request())
        assert index.status == 200
        assert "Spy Clicker" in (server.ASSETS / "index.html").read_text()
        assert "Content-Security-Policy" in index.headers

        health = await server.health(request())
        assert health.status == 200
        assert json.loads(health.text) == {"ok": True, "game_enabled": True}
    finally:
        await service.close()
