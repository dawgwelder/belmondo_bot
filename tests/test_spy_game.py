import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import handlers.spy_game as spy_handlers
from spy_game.director import DirectorDecision, RuleBasedDirector
from spy_game.models import (
    AgencyStatus,
    ChaseStatus,
    ClaimStatus,
    CooperativeStatus,
    DeathOperationStatus,
    DirectorState,
    EconomyStatus,
    EquipmentStatus,
    InterceptStatus,
    NpcStatus,
)
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


class SequenceRandom:
    def __init__(self, *values):
        self.values = iter(values)

    def randint(self, start, end):
        return next(self.values)


def settings(
    tmp_path: Path, *, debounce=0, lifetime=60, equipment_slots=3
) -> SpySettings:
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
        equipment_slots=equipment_slots,
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


async def grant_items(service, user_id, holdings):
    await service.get_profile(
        user_id=user_id,
        username=f"u{user_id}",
        display_name=f"User {user_id}",
        now=NOW,
    )
    await service.database.transaction(
        lambda connection: connection.executemany(
            """
            INSERT INTO user_items(user_id, item_type, amount)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, item_type) DO UPDATE SET amount = excluded.amount
            """,
            [(user_id, item_type, amount) for item_type, amount in holdings.items()],
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


@pytest.mark.asyncio
async def test_director_and_reward_resolver_keep_economy_server_side(tmp_path):
    config = settings(tmp_path)
    state = DirectorState(
        chat_id=CHAT_ID,
        activity_score=10,
        active_players=1,
        minutes_since_last_event=60,
        recent_events=(),
        story_arc=None,
        story_stage=0,
        allowed_events=tuple(item.event_type for item in config.event_weights),
    )
    assert (
        await RuleBasedDirector(config, FixedRandom(1)).choose_event(state)
    ).event_type == "recruitment"
    assert (
        await RuleBasedDirector(config, FixedRandom(9)).choose_event(state)
    ).event_type == "dead_drop"
    assert (
        await RuleBasedDirector(config, FixedRandom(12)).choose_event(state)
    ).event_type == "intercept"
    assert (
        await RuleBasedDirector(config, FixedRandom(15)).choose_event(state)
    ).event_type == "cooperative_operation"
    assert (
        await RuleBasedDirector(config, FixedRandom(17)).choose_event(state)
    ).event_type == "chase"
    assert (
        await RuleBasedDirector(config, FixedRandom(19)).choose_event(state)
    ).event_type == "handler"
    assert (
        await RuleBasedDirector(config, FixedRandom(21)).choose_event(state)
    ).event_type == "npc"
    assert (
        await RuleBasedDirector(config, FixedRandom(22)).choose_event(state)
    ).event_type == "death_operation"
    rare_state = DirectorState(**{**state.__dict__, "recent_events": ("handler",)})
    assert (
        await RuleBasedDirector(config, FixedRandom(5)).choose_event(rare_state)
    ).event_type == "recruitment"
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
async def test_story_hook_loads_matching_lore_for_narrator(tmp_path):
    class StoryDirector:
        async def choose_event(self, _state):
            return DirectorDecision(
                "intercept",
                tone="paranoid",
                story_hook="section_7",
                intensity=2,
            )

    service = SpyGameService(
        settings(tmp_path),
        rng=FixedRandom(),
        director=StoryDirector(),
    )
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await service.record_activity(CHAT_ID, 1, now=NOW)
        await service.record_activity(CHAT_ID, 2, now=NOW)
        await service.tick(now=NOW)
        result = await service.tick(now=NOW + timedelta(seconds=10))
        assert result.spawned[0].story_hook == "section_7"
        assert any("Секция 7" in text for text in result.spawned[0].lore_context)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_concurrent_claim_has_three_winners_and_one_reward_each(tmp_path):
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
        assert len(winners) == 3
        assert (
            sum(result.status is ClaimStatus.ALREADY_RESOLVED for result in results)
            == 47
        )

        assert sorted(result.claims for result in winners) == [1, 2, 3]
        for winner in winners:
            holdings = await service.get_agents(winner.winner_user_id)
            assert [(item.agent_type, item.amount) for item in holdings] == [
                ("informant", 1)
            ]
        state = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT status FROM game_events WHERE id = ?",
                    (event_id,),
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_participants WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("resolved", 3, 3)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_recruitment_counts_each_user_only_once_and_closes_on_third(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (await service.manual_spawn(CHAT_ID, now=NOW)).event

        async def claim(user_id, second):
            return await service.claim_event(
                event_id=event.event_id,
                action="claim",
                chat_id=CHAT_ID,
                user_id=user_id,
                username=f"u{user_id}",
                display_name=f"User {user_id}",
                now=NOW + timedelta(seconds=second),
            )

        first = await claim(1, 1)
        duplicate = await claim(1, 2)
        second = await claim(2, 3)
        assert (first.claims, second.claims) == (1, 2)
        assert duplicate.status is ClaimStatus.ALREADY_CLAIMED
        assert (
            await service.get_chat_status(CHAT_ID)
        ).active_event_id == event.event_id

        third = await claim(3, 4)
        assert (third.status, third.claims, third.required_claims) == (
            ClaimStatus.WON,
            3,
            3,
        )
        assert (await service.get_chat_status(CHAT_ID)).active_event_id is None
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(1)
        ] == [("informant", 1)]
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
                connection.execute(
                    "SELECT COUNT(*) FROM event_participants WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("active", 0, 0, 0)
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
async def test_concurrent_dead_drop_has_one_winner_and_persists_item(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(CHAT_ID, event_type="dead_drop", now=NOW)
        ).event

        async def search(user_id):
            return await service.search_dead_drop(
                event_id=event.event_id,
                chat_id=CHAT_ID,
                user_id=user_id,
                username=f"u{user_id}",
                display_name=f"User {user_id}",
                now=NOW + timedelta(seconds=1),
            )

        results = await asyncio.gather(*(search(user_id) for user_id in range(1, 21)))
        winners = [result for result in results if result.status is ClaimStatus.WON]
        assert len(winners) == 1
        assert (
            sum(result.status is ClaimStatus.ALREADY_RESOLVED for result in results)
            == 19
        )
        winner_id = winners[0].winner_user_id
        assert (
            winners[0].reward.reward_type,
            winners[0].reward.reward_id,
            winners[0].reward.amount,
        ) == ("item", "intel_file", 1)
        inventory = await service.get_inventory(winner_id)
        assert [(item.item_type, item.amount) for item in inventory.items] == [
            ("intel_file", 1)
        ]
        history_count = await service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()[0]
        )
        assert history_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dead_drop_item_failure_rolls_back_event_and_user(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(CHAT_ID, event_type="dead_drop", now=NOW)
        ).event
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_dead_drop_item
                BEFORE INSERT ON user_items
                BEGIN
                    SELECT RAISE(ABORT, 'test item failure');
                END
                """
            ),
            immediate=True,
        )
        with pytest.raises(Exception, match="test item failure"):
            await service.search_dead_drop(
                event_id=event.event_id,
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
                    "SELECT COUNT(*) FROM users WHERE user_id = 1"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("active", 0, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_death_operation_requires_confirmation_and_doubles_stake_on_success(
    tmp_path,
):
    config = settings(tmp_path)
    service = SpyGameService(config, rng=SequenceRandom(1, 0))
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await grant_agents(service, 1, {"informant": 3, "operative": 1})
        event = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="death_operation",
                now=NOW,
            )
        ).event

        confirmation = await service.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username="agent",
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert confirmation.status is DeathOperationStatus.CONFIRMATION_REQUIRED
        assert [(item.agent_type, item.amount) for item in confirmation.staked] == [
            ("informant", 3),
            ("operative", 1),
        ]
        assert [
            (item.agent_type, item.amount) for item in await service.get_agents(1)
        ] == [
            ("informant", 3),
            ("operative", 1),
        ]

        result = await service.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username="agent",
            display_name="Agent",
            now=NOW + timedelta(seconds=2),
        )
        assert result.status is DeathOperationStatus.WON
        assert [(item.agent_type, item.amount) for item in result.rewards] == [
            ("informant", 6),
            ("operative", 2),
            ("analyst", 1),
        ]
        assert [
            (item.agent_type, item.amount) for item in await service.get_agents(1)
        ] == [
            ("analyst", 1),
            ("informant", 6),
            ("operative", 2),
        ]

        duplicate = await service.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username="agent",
            display_name="Agent",
            now=NOW + timedelta(seconds=3),
        )
        assert duplicate.status is DeathOperationStatus.ALREADY_RESOLVED
        history_count = await service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()[0]
        )
        assert history_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_death_operation_failure_loses_all_agents(tmp_path):
    config = settings(tmp_path)
    service = SpyGameService(config, rng=FixedRandom(100))
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await grant_agents(service, 1, {"informant": 4, "courier": 2})
        event = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="death_operation",
                now=NOW,
            )
        ).event
        for offset in (1, 2):
            result = await service.run_death_operation(
                event_id=event.event_id,
                action="death",
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Agent",
                now=NOW + timedelta(seconds=offset),
            )
        assert result.status is DeathOperationStatus.LOST
        assert result.rewards == ()
        assert await service.get_agents(1) == ()
        history = await service.database.read(
            lambda connection: connection.execute(
                "SELECT outcome, reward_type FROM event_history WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
        )
        assert tuple(history) == ("lost", None)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_death_operation_confirmation_survives_restart(tmp_path):
    config = settings(tmp_path)
    first = SpyGameService(config, rng=FixedRandom())
    await first.initialize(now=NOW)
    await first.enable_chat(CHAT_ID, now=NOW)
    await grant_agents(first, 1, {"informant": 2})
    event = (
        await first.manual_spawn(
            CHAT_ID,
            event_type="death_operation",
            now=NOW,
        )
    ).event
    await first.attach_message(event.event_id, 99)
    pending = await first.run_death_operation(
        event_id=event.event_id,
        action="death",
        chat_id=CHAT_ID,
        user_id=1,
        username=None,
        display_name="Agent",
        now=NOW + timedelta(seconds=1),
    )
    assert pending.status is DeathOperationStatus.CONFIRMATION_REQUIRED
    await first.close()

    second = SpyGameService(config, rng=SequenceRandom(1, 0))
    await second.initialize(now=NOW + timedelta(seconds=2))
    try:
        result = await second.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=2),
        )
        assert result.status is DeathOperationStatus.WON
        assert [
            (item.agent_type, item.amount) for item in await second.get_agents(1)
        ] == [
            ("analyst", 1),
            ("informant", 4),
        ]
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_death_operation_needs_agents_and_rolls_back_failed_resolution(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="death_operation",
                now=NOW,
            )
        ).event
        empty = await service.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert empty.status is DeathOperationStatus.INSUFFICIENT_AGENTS

        await grant_agents(service, 1, {"informant": 2})
        confirmation = await service.run_death_operation(
            event_id=event.event_id,
            action="death",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=2),
        )
        assert confirmation.status is DeathOperationStatus.CONFIRMATION_REQUIRED
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_death_operation
                BEFORE UPDATE OF amount ON user_agents
                WHEN NEW.amount = 0
                BEGIN
                    SELECT RAISE(ABORT, 'test death operation failure');
                END
                """
            ),
            immediate=True,
        )
        with pytest.raises(Exception, match="test death operation failure"):
            await service.run_death_operation(
                event_id=event.event_id,
                action="death",
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Agent",
                now=NOW + timedelta(seconds=3),
            )
        state = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT status FROM game_events WHERE id = ?",
                    (event.event_id,),
                ).fetchone()[0],
                connection.execute(
                    "SELECT amount FROM user_agents WHERE user_id = 1 AND agent_type = 'informant'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                    (event.event_id,),
                ).fetchone()[0],
            )
        )
        assert state == ("active", 2, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_intercept_first_answer_resolves_and_correct_choice_rewards_item(
    tmp_path,
):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(CHAT_ID, event_type="intercept", now=NOW)
        ).event
        assert event.config_id == "midnight_frequency"
        result = await service.answer_intercept(
            event_id=event.event_id,
            choice_id="bravo",
            chat_id=CHAT_ID,
            user_id=1,
            username="agent",
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert result.status is InterceptStatus.CORRECT
        assert (result.reward.reward_id, result.reward.amount) == ("access_code", 1)
        inventory = await service.get_inventory(1)
        assert [(item.item_type, item.amount) for item in inventory.items] == [
            ("access_code", 1)
        ]
        duplicate = await service.answer_intercept(
            event_id=event.event_id,
            choice_id="alpha",
            chat_id=CHAT_ID,
            user_id=2,
            username=None,
            display_name="Late",
            now=NOW + timedelta(seconds=2),
        )
        assert duplicate.status is InterceptStatus.ALREADY_RESOLVED
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_intercept_wrong_choice_closes_event_without_reward(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(CHAT_ID, event_type="intercept", now=NOW)
        ).event
        result = await service.answer_intercept(
            event_id=event.event_id,
            choice_id="alpha",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert result.status is InterceptStatus.INCORRECT
        assert result.reward is None
        assert (await service.get_inventory(1)).items == ()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cooperative_operation_rewards_each_distinct_participant_once(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="cooperative_operation",
                now=NOW,
            )
        ).event
        first = await service.contribute_cooperative(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="One",
            now=NOW + timedelta(seconds=1),
        )
        duplicate = await service.contribute_cooperative(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="One",
            now=NOW + timedelta(seconds=2),
        )
        second = await service.contribute_cooperative(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=2,
            username=None,
            display_name="Two",
            now=NOW + timedelta(seconds=3),
        )
        completed = await service.contribute_cooperative(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=3,
            username=None,
            display_name="Three",
            now=NOW + timedelta(seconds=4),
        )
        assert first.status is CooperativeStatus.CONTRIBUTED
        assert duplicate.status is CooperativeStatus.ALREADY_CONTRIBUTED
        assert second.status is CooperativeStatus.CONTRIBUTED
        assert completed.status is CooperativeStatus.COMPLETED
        assert completed.participant_user_ids == (1, 2, 3)
        for user_id in (1, 2, 3):
            assert [
                (item.agent_type, item.amount)
                for item in await service.get_agents(user_id)
            ] == [("informant", 2)]
        history_count = await service.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM event_history WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()[0]
        )
        assert history_count == 6
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_significant_events_advance_persisted_story_arc(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        intercept = (
            await service.manual_spawn(CHAT_ID, event_type="intercept", now=NOW)
        ).event
        await service.answer_intercept(
            event_id=intercept.event_id,
            choice_id="bravo",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="One",
            now=NOW + timedelta(seconds=1),
        )
        stage_one = await service.get_chat_status(CHAT_ID)
        assert (stage_one.story_arc, stage_one.story_stage) == ("mole_hunt", 1)
        assert "Секции 7" in stage_one.story_summary

        cooperative = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="cooperative_operation",
                now=NOW + timedelta(seconds=2),
            )
        ).event
        for user_id in (1, 2, 3):
            await service.contribute_cooperative(
                event_id=cooperative.event_id,
                chat_id=CHAT_ID,
                user_id=user_id,
                username=None,
                display_name=str(user_id),
                now=NOW + timedelta(seconds=2 + user_id),
            )
        assert (await service.get_chat_status(CHAT_ID)).story_stage == 2

        await grant_agents(service, 1, {"informant": 10})
        handler = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="handler",
                now=NOW + timedelta(seconds=6),
            )
        ).event
        exchange = await service.exchange_with_handler(
            event_id=handler.event_id,
            recipe_id="tier2",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="One",
            now=NOW + timedelta(seconds=7),
        )
        assert exchange.status is EconomyStatus.SUCCESS
        final = await service.get_chat_status(CHAT_ID)
        assert final.story_stage == 3
        assert "Ячейка крота раскрыта" in final.story_summary

        await grant_agents(service, 1, {"informant": 20})
        npc = (
            await service.manual_spawn(
                CHAT_ID,
                event_type="npc",
                now=NOW + timedelta(seconds=8),
            )
        ).event
        await service.interact_with_npc(
            event_id=npc.event_id,
            recipe_id="recruiter_network",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="One",
            now=NOW + timedelta(seconds=9),
        )
        epilogue = await service.get_chat_status(CHAT_ID)
        assert epilogue.story_stage == 4
        assert "Поиск крота завершён" in epilogue.story_summary
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_rewards_starter_and_interceptor_in_two_atomic_stages(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = (await service.manual_spawn(CHAT_ID, event_type="chase", now=NOW)).event
        started = await service.advance_chase(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=1,
            username="starter_agent",
            display_name="Starter",
            now=NOW + timedelta(seconds=1),
        )
        assert started.status is ChaseStatus.STARTED
        assert started.starter_user_id == 1
        assert started.starter_name == "@starter_agent"
        assert await service.get_agents(1) == ()

        completed = await service.advance_chase(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=2,
            username="interceptor_agent",
            display_name="Interceptor",
            now=NOW + timedelta(seconds=2),
        )
        assert completed.status is ChaseStatus.COMPLETED
        assert (completed.starter_user_id, completed.interceptor_user_id) == (1, 2)
        assert (completed.starter_name, completed.interceptor_name) == (
            "@starter_agent",
            "@interceptor_agent",
        )
        assert (
            completed.starter_reward.agent_type,
            completed.starter_reward.amount,
        ) == (
            "informant",
            1,
        )
        assert (
            completed.interceptor_reward.agent_type,
            completed.interceptor_reward.amount,
        ) == ("operative", 1)
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(1)
        ] == [("informant", 1)]
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(2)
        ] == [("operative", 1)]

        duplicate = await service.advance_chase(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=3,
            username=None,
            display_name="Late",
            now=NOW + timedelta(seconds=3),
        )
        assert duplicate.status is ChaseStatus.ALREADY_RESOLVED
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_recruiter_npc_atomically_spends_cost_and_applies_agency_bonus(tmp_path):
    config = settings(tmp_path)
    base_reward = RewardResolver(config).resolve_npc(
        config.npc_recipe("recruiter_network"),
        agency_level=0,
        rng=FixedRandom(91),
    )
    boosted_reward = RewardResolver(config).resolve_npc(
        config.npc_recipe("recruiter_network"),
        agency_level=1,
        rng=FixedRandom(91),
    )
    assert config.agent_tier(base_reward.reward_id) == 3
    assert config.agent_tier(boosted_reward.reward_id) == 4

    service = SpyGameService(config, rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await grant_agents(service, 1, {"informant": 20})
        event = (await service.manual_spawn(CHAT_ID, event_type="npc", now=NOW)).event
        assert event.config_id == "recruiter"
        result = await service.interact_with_npc(
            event_id=event.event_id,
            recipe_id="recruiter_network",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Recruit",
            now=NOW + timedelta(seconds=1),
        )
        assert result.status is NpcStatus.SUCCESS
        assert (result.reward.reward_type, result.reward.reward_id) == (
            "agent",
            "operative",
        )
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(1)
        ] == [("operative", 1)]
        duplicate = await service.interact_with_npc(
            event_id=event.event_id,
            recipe_id="recruiter_network",
            chat_id=CHAT_ID,
            user_id=2,
            username=None,
            display_name="Late",
            now=NOW + timedelta(seconds=2),
        )
        assert duplicate.status is NpcStatus.ALREADY_RESOLVED
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_operations_chief_requires_agent_and_item_for_specialist(tmp_path):
    config = settings(tmp_path)
    service = SpyGameService(config, rng=SequenceRandom(1, 1))
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await grant_agents(service, 1, {"operative": 1})
        await grant_items(service, 1, {"fake_passport": 1})
        event = (await service.manual_spawn(CHAT_ID, event_type="npc", now=NOW)).event
        assert event.config_id == "operations_chief"
        result = await service.interact_with_npc(
            event_id=event.event_id,
            recipe_id="chief_illegal",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Chief",
            now=NOW + timedelta(seconds=1),
        )
        assert result.status is NpcStatus.SUCCESS
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(1)
        ] == [("illegal_agent", 1)]
        assert (await service.get_inventory(1)).items == ()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_npc_failure_rolls_back_event_cost_and_reward(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(service, 1, {"informant": 20})
        event = (await service.manual_spawn(CHAT_ID, event_type="npc", now=NOW)).event
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_npc_history
                BEFORE INSERT ON event_history
                WHEN NEW.event_type = 'npc'
                BEGIN
                    SELECT RAISE(ABORT, 'test NPC failure');
                END
                """
            ),
            immediate=True,
        )
        with pytest.raises(Exception, match="test NPC failure"):
            await service.interact_with_npc(
                event_id=event.event_id,
                recipe_id="recruiter_network",
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Recruit",
                now=NOW + timedelta(seconds=1),
            )
        state = await service.database.read(
            lambda connection: (
                connection.execute(
                    "SELECT status FROM game_events WHERE id = ?",
                    (event.event_id,),
                ).fetchone()[0],
                connection.execute(
                    "SELECT amount FROM user_agents WHERE user_id = 1 AND agent_type = 'informant'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT COUNT(*) FROM user_agents WHERE user_id = 1 AND agent_type = 'operative'"
                ).fetchone()[0],
            )
        )
        assert state == ("active", 20, 0)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_endgame_founds_agency_and_resets_only_required_progress(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        blocked = await service.found_agency(
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Director",
            expected_agency_level=0,
            now=NOW,
        )
        assert blocked.status is AgencyStatus.INSUFFICIENT_RESOURCES
        await grant_agents(
            service,
            1,
            {
                "intelligence_director": 1,
                "resident": 2,
                "illegal_agent": 2,
                "ghost_agent": 1,
            },
        )
        await service.database.transaction(
            lambda connection: connection.execute(
                "UPDATE users SET reputation = 3 WHERE user_id = 1"
            ),
            immediate=True,
        )
        founded = await service.found_agency(
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Director",
            expected_agency_level=0,
            now=NOW + timedelta(seconds=1),
        )
        assert (founded.status, founded.agency_level) == (AgencyStatus.SUCCESS, 1)
        profile = await service.get_profile(
            user_id=1,
            username=None,
            display_name="Director",
            now=NOW + timedelta(seconds=2),
        )
        assert (profile.reputation, profile.agency_level) == (0, 1)
        assert [
            (holding.agent_type, holding.amount)
            for holding in await service.get_agents(1)
        ] == [("ghost_agent", 1)]
        stale = await service.found_agency(
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Director",
            expected_agency_level=0,
            now=NOW + timedelta(seconds=3),
        )
        assert stale.status is AgencyStatus.STALE
        history = await service.database.read(
            lambda connection: connection.execute(
                "SELECT from_level, to_level FROM agency_history WHERE user_id = 1"
            ).fetchall()
        )
        assert [tuple(row) for row in history] == [(0, 1)]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_endgame_audit_failure_rolls_back_complete_transition(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        requirements = {
            "intelligence_director": 1,
            "resident": 2,
            "illegal_agent": 2,
        }
        await grant_agents(service, 1, requirements)
        await service.database.transaction(
            lambda connection: connection.execute(
                "UPDATE users SET reputation = 3 WHERE user_id = 1"
            ),
            immediate=True,
        )
        await service.database.transaction(
            lambda connection: connection.execute(
                """
                CREATE TRIGGER reject_agency_history
                BEFORE INSERT ON agency_history
                BEGIN
                    SELECT RAISE(ABORT, 'test agency failure');
                END
                """
            ),
            immediate=True,
        )
        with pytest.raises(Exception, match="test agency failure"):
            await service.found_agency(
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name="Director",
                expected_agency_level=0,
                now=NOW + timedelta(seconds=1),
            )
        profile = await service.get_profile(
            user_id=1,
            username=None,
            display_name="Director",
            now=NOW + timedelta(seconds=2),
        )
        assert (profile.reputation, profile.agency_level) == (3, 0)
        assert {
            holding.agent_type: holding.amount
            for holding in await service.get_agents(1)
        } == requirements
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_leaderboard_has_deterministic_progress_order(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(service, 1, {"informant": 20})
        await grant_agents(service, 2, {"analyst": 2})
        await service.database.transaction(
            lambda connection: connection.execute(
                "UPDATE users SET reputation = 1 WHERE user_id = 2"
            ),
            immediate=True,
        )
        entries = await service.get_leaderboard()
        assert [(entry.user_id, entry.rank) for entry in entries] == [(2, 1), (1, 2)]
        assert (entries[0].rare_agents, entries[0].total_agents) == (2, 2)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_equipment_slots_and_wiretap_modify_recruitment_reward(tmp_path):
    config = settings(tmp_path, equipment_slots=2)
    service = SpyGameService(config, rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    try:
        await grant_items(
            service,
            1,
            {
                "fake_passport": 1,
                "radio": 1,
                "wiretap": 1,
                "intel_file": 1,
            },
        )
        consumable = await service.equip_item(
            chat_id=CHAT_ID, user_id=1, item_type="intel_file"
        )
        passport = await service.equip_item(
            chat_id=CHAT_ID, user_id=1, item_type="fake_passport"
        )
        radio = await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="radio")
        full = await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="wiretap")
        assert consumable.status is EquipmentStatus.NOT_EQUIPMENT
        assert (passport.status, passport.slot) == (EquipmentStatus.SUCCESS, 1)
        assert (radio.status, radio.slot) == (EquipmentStatus.SUCCESS, 2)
        assert full.status is EquipmentStatus.NO_FREE_SLOT

        removed = await service.unequip_item(chat_id=CHAT_ID, user_id=1, slot=1)
        wiretap = await service.equip_item(
            chat_id=CHAT_ID, user_id=1, item_type="wiretap"
        )
        assert removed.item_type == "fake_passport"
        assert (wiretap.status, wiretap.slot) == (EquipmentStatus.SUCCESS, 1)

        event = (await service.manual_spawn(CHAT_ID, now=NOW)).event
        claim = await service.claim_event(
            event_id=event.event_id,
            action="claim",
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name="Agent",
            now=NOW + timedelta(seconds=1),
        )
        assert claim.reward.amount == 2
        inventory = await service.get_inventory(1)
        assert [(item.slot, item.item_type) for item in inventory.equipped] == [
            (1, "wiretap"),
            (2, "radio"),
        ]
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
        assert migration_count == 6
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_existing_version_one_database_upgrades_to_current_schema(tmp_path):
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
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'user_items'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'equipped_items'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'event_participants'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'story_summary'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'lore'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'event_templates'"
                ).fetchone()[0],
                connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agency_history'"
                ).fetchone()[0],
            )
        )
        assert state == (
            [1, 2, 3, 4, 5, 6],
            "economy_history",
            "user_items",
            "equipped_items",
            "event_participants",
            "story_summary",
            "lore",
            "event_templates",
            "agency_history",
        )
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
            "spy:menu:inventory",
            "spy:menu:leaderboard",
            "spy:menu:status",
            "spy:menu:refresh",
            "spy:prestige:0",
            "spy:agency:status",
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
        assert all("10" not in row[0]["callback_data"].split(":")[1] for row in buttons)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_npc_event_publishes_only_selected_server_side_recipes(
    tmp_path, monkeypatch
):
    service = await initialized_service(tmp_path)
    event = (await service.manual_spawn(CHAT_ID, event_type="npc", now=NOW)).event
    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 48}})
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
            f"spy:npc_recruiter_network:{event.event_id}"
        ]
        assert "20" not in buttons[0][0]["callback_data"]
        assert "РЕКРУТЕР" in send_rich.await_args.args[2][0]["text"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_dead_drop_event_publishes_only_opaque_search_action(
    tmp_path, monkeypatch
):
    service = await initialized_service(tmp_path)
    event = (await service.manual_spawn(CHAT_ID, event_type="dead_drop", now=NOW)).event
    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 46}})
    monkeypatch.setattr(spy_handlers, "send_rich_message", send_rich)
    context = SimpleNamespace(
        bot_data={"spy_game": service},
        bot=SimpleNamespace(token="TOKEN", send_message=AsyncMock()),
    )
    try:
        await spy_handlers.publish_spy_event(context, event)
        kwargs = send_rich.await_args.kwargs
        button = kwargs["reply_markup"]["inline_keyboard"][0][0]
        assert button["callback_data"] == f"spy:search:{event.event_id}"
        assert "intel_file" not in button["callback_data"]
        assert "ТАЙНИК" in send_rich.await_args.args[2][0]["text"]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_death_operation_publishes_opaque_all_in_action(tmp_path, monkeypatch):
    service = await initialized_service(tmp_path)
    event = (
        await service.manual_spawn(
            CHAT_ID,
            event_type="death_operation",
            now=NOW,
        )
    ).event
    send_rich = AsyncMock(return_value={"ok": True, "result": {"message_id": 47}})
    monkeypatch.setattr(spy_handlers, "send_rich_message", send_rich)
    context = SimpleNamespace(
        bot_data={"spy_game": service},
        bot=SimpleNamespace(token="TOKEN", send_message=AsyncMock()),
    )
    try:
        await spy_handlers.publish_spy_event(context, event)
        kwargs = send_rich.await_args.kwargs
        button = kwargs["reply_markup"]["inline_keyboard"][0][0]
        assert button["callback_data"] == f"spy:death:{event.event_id}"
        assert "35" not in button["callback_data"]
        assert "СМЕРТЕЛЬНАЯ ОПЕРАЦИЯ" in send_rich.await_args.args[2][0]["text"]
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
