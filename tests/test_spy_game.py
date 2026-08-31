import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.spy_game as spy_handlers
from spy_game.director import RuleBasedDirector
from spy_game.models import ClaimStatus, EconomyStatus
from spy_game.rewards import RewardResolver
from spy_game.scheduler import ActivityPolicy
from spy_game.service import SpyGameService
from spy_game.settings import ActivityBand, SpySettings


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
CHAT_ID = -100123


class FixedRandom:
    def __init__(self, value=None):
        self.value = value

    def randint(self, start, end):
        return self.value if self.value is not None else start


def settings(tmp_path: Path, *, debounce=0, lifetime=60) -> SpySettings:
    return SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "spy.sqlite3",
        allowed_chat_ids=frozenset({CHAT_ID}),
        event_lifetime_seconds=lifetime,
        activity_threshold=2,
        activity_user_debounce_seconds=debounce,
        activity_half_life_seconds=100,
        activity_bands=(ActivityBand(2, 10, 10),),
        allow_manual_spawn=True,
    )


async def initialized_service(tmp_path, **kwargs):
    service = SpyGameService(settings(tmp_path, **kwargs), rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    return service


async def grant_agents(service, user_id, holdings):
    await service.get_profile(
        user_id=user_id,
        username=f"u{user_id}",
        display_name=f"User {user_id}",
        now=NOW,
    )
    await service.database.transaction(
        lambda connection: connection.executemany(
            """
            INSERT INTO user_agents(user_id, agent_type, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, agent_type) DO UPDATE SET amount = excluded.amount
            """,
            [(user_id, agent_type, amount) for agent_type, amount in holdings.items()],
        ),
        immediate=True,
    )


async def spawn_event(service, event_type):
    return await service.database.transaction(
        lambda connection: service.repository.manual_spawn(
            connection,
            CHAT_ID,
            NOW,
            event_type,
        ),
        immediate=True,
    )


def test_director_and_reward_resolver_keep_economy_server_side(tmp_path):
    config = settings(tmp_path)
    assert (
        RuleBasedDirector(config, FixedRandom(1)).choose_event().event_type
        == "recruitment"
    )
    assert (
        RuleBasedDirector(config, FixedRandom(5)).choose_event().event_type == "handler"
    )
    assert (
        RuleBasedDirector(config, FixedRandom(5))
        .choose_event(previous_event_type="handler")
        .event_type
        == "recruitment"
    )
    reward = RewardResolver(config).resolve("recruitment", reputation=2)
    assert (reward.agent_type, reward.amount) == ("informant", 3)


@pytest.mark.asyncio
async def test_activity_assigns_persisted_timer_and_spawns_when_due(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        assert await service.record_activity(CHAT_ID, 1, now=NOW)
        assert await service.record_activity(CHAT_ID, 2, now=NOW)

        first_tick = await service.tick(now=NOW)
        assert first_tick.spawned == ()
        status = await service.get_chat_status(CHAT_ID)
        assert status.activity_score == 2
        assert status.next_event_at == NOW + timedelta(seconds=10)

        early = await service.tick(now=NOW + timedelta(seconds=9))
        assert early.spawned == ()
        due = await service.tick(now=NOW + timedelta(seconds=10))
        assert len(due.spawned) == 1
        assert due.spawned[0].chat_id == CHAT_ID

        after = await service.get_chat_status(CHAT_ID)
        assert after.next_event_at is None
        assert after.active_event_id == due.spawned[0].event_id
        assert after.activity_score == pytest.approx(0.9)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_activity_debounce_and_decay_are_deterministic(tmp_path):
    config = settings(tmp_path, debounce=20)
    policy = ActivityPolicy(config)
    assert policy.update_score(8, NOW, 0, NOW + timedelta(seconds=100)) == 4

    service = SpyGameService(config, rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        assert await service.record_activity(CHAT_ID, 1, now=NOW)
        assert not await service.record_activity(
            CHAT_ID, 1, now=NOW + timedelta(seconds=19)
        )
        assert await service.record_activity(
            CHAT_ID, 1, now=NOW + timedelta(seconds=20)
        )
        await service.tick(now=NOW + timedelta(seconds=20))
        status = await service.get_chat_status(CHAT_ID)
        assert status.activity_score == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_claim_has_one_winner_and_one_reward(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        spawned = await service.manual_spawn(CHAT_ID, now=NOW)
        event_id = spawned.event.event_id

        async def claim(user_id):
            return await service.claim_event(
                event_id=event_id,
                action="claim",
                chat_id=CHAT_ID,
                user_id=user_id,
                username=f"u{user_id}",
                display_name=f"User {user_id}",
                now=NOW + timedelta(seconds=1),
            )

        results = await asyncio.gather(*(claim(user_id) for user_id in range(1, 51)))
        winners = [result for result in results if result.status is ClaimStatus.WON]
        assert len(winners) == 1
        assert (
            sum(result.status is ClaimStatus.ALREADY_RESOLVED for result in results)
            == 49
        )

        winner_id = winners[0].winner_user_id
        holdings = await service.get_agents(winner_id)
        assert [(item.agent_type, item.amount) for item in holdings] == [
            ("informant", 1)
        ]
        history_count = await service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                (event_id,),
            ).fetchone()[0]
        )
        assert history_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_reward_failure_rolls_back_claim_and_user(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        spawned = await service.manual_spawn(CHAT_ID, now=NOW)
        event_id = spawned.event.event_id
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_spy_reward
                BEFORE INSERT ON user_agents
                BEGIN
                    SELECT RAISE(ABORT, 'test reward failure');
                END
                """
            ),
            immediate=True,
        )

        with pytest.raises(Exception, match="test reward failure"):
            await service.claim_event(
                event_id=event_id,
                action="claim",
                chat_id=CHAT_ID,
                user_id=7,
                username="rollback",
                display_name="Rollback",
                now=NOW + timedelta(seconds=1),
            )

        state = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT status FROM game_events WHERE id = ?", (event_id,)
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM users WHERE user_id = 7"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("active", 0, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_handler_exchange_is_atomic_and_idempotent(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (await spawn_event(service, "handler")).event

        insufficient = await service.exchange_with_handler(
            event_id=event.event_id,
            recipe_id="tier2",
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            now=NOW + timedelta(seconds=1),
        )
        assert insufficient.status is EconomyStatus.INSUFFICIENT_RESOURCES
        assert [(item.agent_type, item.amount) for item in insufficient.required] == [
            ("informant", 10)
        ]
        assert (
            await service.get_chat_status(CHAT_ID)
        ).active_event_id == event.event_id

        await grant_agents(service, 1, {"informant": 10})
        exchanged = await service.exchange_with_handler(
            event_id=event.event_id,
            recipe_id="tier2",
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            now=NOW + timedelta(seconds=2),
        )
        duplicate = await service.exchange_with_handler(
            event_id=event.event_id,
            recipe_id="tier2",
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            now=NOW + timedelta(seconds=3),
        )

        assert exchanged.status is EconomyStatus.SUCCESS
        assert (exchanged.reward.agent_type, exchanged.reward.amount) == (
            "operative",
            1,
        )
        assert duplicate.status is EconomyStatus.ALREADY_RESOLVED
        assert [
            (item.agent_type, item.amount) for item in await service.get_agents(1)
        ] == [("operative", 1)]
        history = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT outcome FROM event_history WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM economy_history WHERE source_event_id = ?",
                    (event.event_id,),
                ).fetchone()[0],
            )
        )
        assert history == ("exchanged", 1)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_handler_can_create_tier_three_agent(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(
            service,
            1,
            {"operative": 2, "observer": 2, "courier": 2},
        )
        event = (await spawn_event(service, "handler")).event
        result = await service.exchange_with_handler(
            event_id=event.event_id,
            recipe_id="tier3",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert result.status is EconomyStatus.SUCCESS
        assert result.reward.agent_type == "analyst"
        assert [
            (item.agent_type, item.amount) for item in await service.get_agents(1)
        ] == [("analyst", 1)]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_handler_failure_rolls_back_event_cost_and_reward(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(service, 1, {"informant": 10})
        event = (await spawn_event(service, "handler")).event
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_economy_history
                BEFORE INSERT ON economy_history
                BEGIN
                    SELECT RAISE(ABORT, 'test economy failure');
                END
                """
            ),
            immediate=True,
        )

        with pytest.raises(Exception, match="test economy failure"):
            await service.exchange_with_handler(
                event_id=event.event_id,
                recipe_id="tier2",
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Agent",
                now=NOW + timedelta(seconds=1),
            )

        state = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT status FROM game_events WHERE id = ?", (event.event_id,)
                ).fetchone()[0],
                connection.execute(
                    "SELECT amount FROM user_agents WHERE user_id = 1 AND agent_type = 'informant'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM user_agents WHERE user_id = 1 AND agent_type = 'operative'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("active", 10, 0, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_prestige_spends_agents_once_and_increases_future_rewards(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(
            service,
            1,
            {"operative": 1, "observer": 1, "courier": 1},
        )
        result = await service.increase_reputation(
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            expected_reputation=0,
            now=NOW + timedelta(seconds=1),
        )
        duplicate = await service.increase_reputation(
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            expected_reputation=0,
            now=NOW + timedelta(seconds=2),
        )
        assert result.status is EconomyStatus.SUCCESS
        assert result.reputation == 1
        assert duplicate.status is EconomyStatus.STALE
        assert await service.get_agents(1) == ()

        event = (
            await service.manual_spawn(CHAT_ID, now=NOW + timedelta(seconds=3))
        ).event
        claim = await service.claim_event(
            event_id=event.event_id,
            action="claim",
            chat_id=CHAT_ID,
            user_id=1,
            username="bond",
            display_name="James",
            now=NOW + timedelta(seconds=4),
        )
        assert claim.reward.amount == 2
        profile = await service.get_profile(
            user_id=1,
            username="bond",
            display_name="James",
            now=NOW + timedelta(seconds=5),
        )
        assert profile.reputation == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_prestige_failure_rolls_back_cost_and_reputation(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(
            service,
            1,
            {"operative": 1, "observer": 1, "courier": 1},
        )
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_prestige_history
                BEFORE INSERT ON economy_history
                WHEN NEW.action = 'prestige'
                BEGIN
                    SELECT RAISE(ABORT, 'test prestige failure');
                END
                """
            ),
            immediate=True,
        )
        with pytest.raises(Exception, match="test prestige failure"):
            await service.increase_reputation(
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Agent",
                expected_reputation=0,
                now=NOW + timedelta(seconds=1),
            )
        profile = await service.get_profile(
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=2),
        )
        assert profile.reputation == 0
        assert sorted(
            (item.agent_type, item.amount) for item in await service.get_agents(1)
        ) == [("courier", 1), ("observer", 1), ("operative", 1)]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expired_event_cannot_be_claimed(tmp_path):
    service = await initialized_service(tmp_path, lifetime=5)
    try:
        spawned = await service.manual_spawn(CHAT_ID, now=NOW)
        result = await service.claim_event(
            event_id=spawned.event.event_id,
            action="claim",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Late",
            now=NOW + timedelta(seconds=5),
        )
        assert result.status is ClaimStatus.EXPIRED
        assert await service.get_agents(1) == ()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_migrations_are_idempotent_and_progress_survives_restart(tmp_path):
    config = settings(tmp_path)
    first = SpyGameService(config, rng=FixedRandom())
    await first.initialize(now=NOW)
    await first.enable_chat(CHAT_ID, now=NOW)
    spawned = await first.manual_spawn(CHAT_ID, now=NOW)
    await first.claim_event(
        event_id=spawned.event.event_id,
        action="claim",
        chat_id=CHAT_ID,
        user_id=1,
        username="agent",
        display_name="Agent",
        now=NOW + timedelta(seconds=1),
    )
    await first.close()

    second = SpyGameService(config, rng=FixedRandom())
    await second.initialize(now=NOW + timedelta(seconds=2))
    try:
        assert [
            (item.agent_type, item.amount) for item in await second.get_agents(1)
        ] == [("informant", 1)]
        migration_count = await second.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
        )
        assert migration_count == 2
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_existing_version_one_database_upgrades_to_economy_schema(tmp_path):
    config = settings(tmp_path)
    migration = (
        Path(__file__).parents[1] / "spy_game" / "migrations" / "001_initial.sql"
    ).read_text(encoding="utf-8")
    with sqlite3.connect(config.database_path) as connection:
        connection.execute(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
            """
        )
        connection.executescript(migration)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (1, ?)",
            (NOW.isoformat(),),
        )

    service = SpyGameService(config, rng=FixedRandom())
    await service.initialize(now=NOW)
    try:
        state = await service.database.read(
            lambda connection: (
                [
                    row[0]
                    for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    ).fetchall()
                ],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'economy_history'"
                ).fetchone()[0],
            )
        )
        assert state == ([1, 2], "economy_history")
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_restart_reports_expired_message_for_keyboard_cleanup(tmp_path):
    config = settings(tmp_path, lifetime=5)
    first = SpyGameService(config, rng=FixedRandom())
    await first.initialize(now=NOW)
    await first.enable_chat(CHAT_ID, now=NOW)
    spawned = await first.manual_spawn(CHAT_ID, now=NOW)
    await first.attach_message(spawned.event.event_id, 777)
    await first.close()

    second = SpyGameService(config, rng=FixedRandom())
    await second.initialize(now=NOW + timedelta(seconds=10))
    try:
        tick = await second.tick(now=NOW + timedelta(seconds=10))
        assert [(event.event_id, event.message_id) for event in tick.expired] == [
            (spawned.event.event_id, 777)
        ]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_restart_cancels_event_that_was_never_published(tmp_path):
    config = settings(tmp_path)
    first = SpyGameService(config, rng=FixedRandom())
    await first.initialize(now=NOW)
    await first.enable_chat(CHAT_ID, now=NOW)
    spawned = await first.manual_spawn(CHAT_ID, now=NOW)
    await first.close()

    second = SpyGameService(config, rng=FixedRandom())
    await second.initialize(now=NOW + timedelta(seconds=1))
    try:
        status = await second.get_chat_status(CHAT_ID)
        assert status.active_event_id is None
        outcome = await second.database.read(
            lambda connection: connection.execute(
                "SELECT outcome FROM event_history WHERE event_id = ?",
                (spawned.event.event_id,),
            ).fetchone()[0]
        )
        assert outcome == "startup_reconciliation"
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_restart_cancels_invalid_persisted_event_payload(tmp_path):
    config = settings(tmp_path)
    first = SpyGameService(config, rng=FixedRandom())
    await first.initialize(now=NOW)
    await first.enable_chat(CHAT_ID, now=NOW)
    spawned = await first.manual_spawn(CHAT_ID, now=NOW)
    await first.attach_message(spawned.event.event_id, 778)
    await first.database.transaction(
        lambda connection: connection.execute(
            "UPDATE game_events SET payload_json = '{}' WHERE id = ?",
            (spawned.event.event_id,),
        ),
        immediate=True,
    )
    await first.close()

    second = SpyGameService(config, rng=FixedRandom())
    await second.initialize(now=NOW + timedelta(seconds=1))
    try:
        tick = await second.tick(now=NOW + timedelta(seconds=1))
        assert [(event.event_id, event.message_id) for event in tick.expired] == [
            (spawned.event.event_id, 778)
        ]
        outcome = await second.database.read(
            lambda connection: connection.execute(
                "SELECT outcome FROM event_history WHERE event_id = ?",
                (spawned.event.event_id,),
            ).fetchone()[0]
        )
        assert outcome == "invalid_payload"
    finally:
        await second.close()


def test_rich_menu_contains_profile_and_countdown():
    profile = SimpleNamespace(total_agents=3, reputation=1, agency_level=0)
    status = SimpleNamespace(
        active_event_id=None,
        enabled=True,
        activity_score=7.5,
        next_event_at=NOW + timedelta(minutes=12),
    )
    blocks = spy_handlers.build_menu_blocks(profile, status, NOW)
    assert blocks[0]["text"] == "🕵️ SPY CLICKER · ОПЕРАТИВНЫЙ ЦЕНТР"
    assert "примерно через 12 мин" in blocks[2]["text"]
    assert blocks[-1]["type"] == "footer"


@pytest.mark.asyncio
async def test_player_has_one_menu_command_with_inline_navigation(
    tmp_path, monkeypatch
):
    service = await initialized_service(tmp_path)
    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 44}})
    monkeypatch.setattr(spy_handlers, "send_rich_message", send_rich)
    user = SimpleNamespace(id=1, username="bond", full_name="James", is_bot=False)
    update = SimpleNamespace(
        effective_user=user,
        effective_chat=SimpleNamespace(id=CHAT_ID, type="supergroup"),
    )
    context = SimpleNamespace(
        bot_data={"spy_game": service, "paused": False},
        bot=SimpleNamespace(token="TOKEN", send_message=AsyncMock()),
    )
    try:
        await spy_handlers.spy_menu(update, context)
        kwargs = send_rich.await_args.kwargs
        buttons = kwargs["reply_markup"]["inline_keyboard"]
        callback_data = [button["callback_data"] for row in buttons for button in row]
        assert callback_data == [
            "spy:menu:profile",
            "spy:menu:agents",
            "spy:menu:status",
            "spy:menu:refresh",
            "spy:prestige:0",
        ]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_handler_event_publishes_server_side_exchange_recipes(
    tmp_path, monkeypatch
):
    service = await initialized_service(tmp_path)
    event = (await service.manual_spawn(CHAT_ID, event_type="handler", now=NOW)).event
    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 45}})
    monkeypatch.setattr(spy_handlers, "send_rich_message", send_rich)
    context = SimpleNamespace(
        bot_data={"spy_game": service},
        bot=SimpleNamespace(token="TOKEN", send_message=AsyncMock()),
    )
    try:
        await spy_handlers.publish_spy_event(context, event)
        kwargs = send_rich.await_args.kwargs
        buttons = kwargs["reply_markup"]["inline_keyboard"]
        assert [row[0]["callback_data"] for row in buttons] == [
            f"spy:exchange_tier2:{event.event_id}",
            f"spy:exchange_operative:{event.event_id}",
            f"spy:exchange_observer:{event.event_id}",
            f"spy:exchange_courier:{event.event_id}",
            f"spy:exchange_tier3:{event.event_id}",
        ]
        assert all("10" not in row[0]["callback_data"] for row in buttons)
    finally:
        await service.close()


def test_enabled_settings_require_explicit_allowlist(tmp_path):
    with pytest.raises(ValueError, match="allowlist"):
        SpySettings(
            mode="prod",
            enabled=True,
            database_path=tmp_path / "spy.sqlite3",
            allowed_chat_ids=frozenset(),
        )


@pytest.mark.asyncio
async def test_spy_admin_rejects_non_master_without_touching_service():
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=SimpleNamespace(id=CHAT_ID, type="supergroup"),
        effective_message=SimpleNamespace(reply_text=reply_text),
    )
    context = SimpleNamespace(bot_data={"master": 999}, args=["enable"])

    await spy_handlers.spy_admin(update, context)

    reply_text.assert_awaited_once_with("Команда доступна только master.")
