import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from handlers.spy.chase import chase_tick
from spy_game.models import ChaseStatus, NpcStatus
from spy_game.service import SpyGameService
from test_spy_game import (
    CHAT_ID,
    NOW,
    FixedRandom,
    initialized_service,
    grant_agents,
    grant_items,
)


async def chase(service):
    event = (await service.manual_spawn(CHAT_ID, event_type="chase", now=NOW)).event
    await service.attach_message(event.event_id, 42)
    return event.event_id


async def press(service, event_id, user=1, seconds=0, chat_id=CHAT_ID):
    return await service.advance_chase(
        event_id=event_id,
        chat_id=chat_id,
        user_id=user,
        username=f"agent{user}",
        display_name=None,
        now=NOW + timedelta(seconds=seconds),
    )


@pytest.mark.asyncio
async def test_chase_same_leader_cannot_extend_timer_and_exact_deadline_settles(
    tmp_path,
):
    service = await initialized_service(tmp_path)
    try:
        event = await chase(service)
        first = await press(service, event)
        repeat = await press(service, event, seconds=29)
        assert repeat.status is ChaseStatus.ALREADY_LEADING
        assert repeat.deadline == first.deadline == NOW + timedelta(seconds=30)
        assert repeat.turn == 1 and await service.get_agents(1) == ()
        late = await press(service, event, user=2, seconds=30)
        assert late.status is ChaseStatus.COMPLETED and late.leader_user_id == 1
        assert [(a.agent_type, a.amount) for a in await service.get_agents(1)] == [
            ("informant", 1)
        ]
        assert await service.get_agents(2) == ()
        assert (
            await press(service, event, user=3, seconds=31)
        ).status is ChaseStatus.ALREADY_RESOLVED
        await service.settle_chases(now=NOW + timedelta(seconds=40))
        assert (await service.get_agents(1))[0].amount == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_six_turns_varied_growing_prize_only_last_leader_wins(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = await chase(service)
        previous = ()
        for turn, seconds in enumerate((30, 25, 20, 15, 10, 5), 1):
            result = await press(
                service, event, user=1 if turn % 2 else 2, seconds=turn
            )
            assert result.status is ChaseStatus.STARTED and result.turn == turn
            assert result.deadline == NOW + timedelta(seconds=turn + seconds)
            assert result.rewards[:-1] == previous
            previous = result.rewards
        assert {r.reward_type for r in previous} == {"item", "agent"}
        assert any(r.reward_id == "analyst" for r in previous)
        assert (
            await press(service, event, user=3, seconds=7)
        ).status is ChaseStatus.LIMIT_REACHED
        assert await service.get_agents(2) == ()
        result = (await service.settle_chases(now=NOW + timedelta(seconds=11)))[0]
        assert result.status is ChaseStatus.COMPLETED and result.leader_user_id == 2
        assert await service.get_agents(1) == ()
        assert sum(a.amount for a in await service.get_agents(2)) == 4
        assert sum(i.amount for i in (await service.get_inventory(2)).items) == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_concurrent_same_user_does_not_double_advance(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = await chase(service)
        results = await asyncio.gather(*(press(service, event) for _ in range(8)))
        assert sum(r.status is ChaseStatus.STARTED for r in results) == 1
        assert sum(r.status is ChaseStatus.ALREADY_LEADING for r in results) == 7
        assert (await service.get_chase(event)).turn == 1
        await asyncio.gather(
            *(press(service, event, user=uid, seconds=1) for uid in range(2, 12))
        )
        assert (await service.get_chase(event)).turn == 6
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_restart_pays_overdue_and_retains_pending_notification(tmp_path):
    service = await initialized_service(tmp_path)
    event = await chase(service)
    await press(service, event)
    config = service.settings
    await service.close()
    restored = SpyGameService(config, rng=FixedRandom())
    try:
        await restored.initialize(now=NOW + timedelta(seconds=31))
        assert (await restored.get_agents(1))[0].amount == 1
        for _ in range(2):
            assert (
                len(await restored.settle_chases(now=NOW + timedelta(seconds=32))) == 1
            )
        await restored.mark_chase_notified(event, now=NOW + timedelta(seconds=32))
        assert await restored.settle_chases(now=NOW + timedelta(seconds=33)) == ()
        assert (await restored.get_agents(1))[0].amount == 1
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_chase_restart_before_deadline_and_atomic_reward_rollback(
    tmp_path, monkeypatch
):
    service = await initialized_service(tmp_path)
    event = await chase(service)
    await press(service, event)
    config = service.settings
    await service.close()
    service = SpyGameService(config, rng=FixedRandom())
    try:
        await service.initialize(now=NOW + timedelta(seconds=5))
        assert (await service.get_chase(event)).deadline == NOW + timedelta(seconds=30)
        await press(service, event, user=2, seconds=10)
        original = service.repository.economy.add_drop_reward

        def fail_after_write(*args):
            original(*args)
            raise RuntimeError("failure after credit")

        monkeypatch.setattr(
            service.repository.economy, "add_drop_reward", fail_after_write
        )
        with pytest.raises(RuntimeError):
            await service.settle_chases(now=NOW + timedelta(seconds=35))
        assert (await service.get_chase(event)).status is ChaseStatus.STARTED
        assert await service.get_agents(2) == ()
        monkeypatch.setattr(service.repository.economy, "add_drop_reward", original)
        await service.settle_chases(now=NOW + timedelta(seconds=35))
        assert len(await service.get_agents(2)) == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_wrong_chat_disabled_and_unstarted_expiry(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        event = await chase(service)
        assert (await press(service, event, chat_id=999)).status is ChaseStatus.DISABLED
        assert (await press(service, event, seconds=60)).status is ChaseStatus.EXPIRED
        assert await service.get_agents(1) == ()
        await service.disable_chat(CHAT_ID, now=NOW + timedelta(seconds=61))
        assert (await press(service, event)).status is ChaseStatus.DISABLED
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_chase_telegram_result_delivery_retries_without_double_reward(
    tmp_path, monkeypatch
):
    from handlers.spy import chase as adapter

    service = await initialized_service(tmp_path)
    try:
        event = await chase(service)
        await press(service, event)
        await service.settle_chases(now=NOW + timedelta(seconds=30))
        monkeypatch.setattr(adapter, "_service", lambda context: service)
        bot = SimpleNamespace(
            edit_message_text=AsyncMock(side_effect=[RuntimeError("offline"), None])
        )
        context = SimpleNamespace(bot=bot, bot_data={})
        await chase_tick(context)
        await chase_tick(context)
        assert bot.edit_message_text.await_count == 2
        assert "@agent1" in bot.edit_message_text.call_args.kwargs["text"]
        assert bot.edit_message_text.call_args.kwargs["reply_markup"] is None
        assert await service.settle_chases(now=NOW + timedelta(seconds=40)) == ()
        assert (await service.get_agents(1))[0].amount == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_passport_exchange_costs_idempotency_and_insufficient_resources(tmp_path):
    service = await initialized_service(tmp_path)
    try:
        await grant_agents(service, 1, {"informant": 20})
        await grant_items(service, 1, {"intel_file": 2})
        args = dict(
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name=None,
            recipe_id="counter_passport",
            operation_id="passport-test",
            now=NOW,
        )
        first, repeated = await asyncio.gather(
            service.exchange_with_contact(**args), service.exchange_with_contact(**args)
        )
        assert first.status is repeated.status is NpcStatus.SUCCESS
        assert first.reward.reward_id == "fake_passport"
        assert (await service.get_agents(1))[0].amount == 10
        items = {
            item.item_type: item.amount
            for item in (await service.get_inventory(1)).items
        }
        assert items.get("fake_passport") == 1 and items.get("intel_file", 0) == 0
        args["operation_id"] = "second-passport"
        assert (
            await service.exchange_with_contact(**args)
        ).status is NpcStatus.INSUFFICIENT_RESOURCES
    finally:
        await service.close()


def test_passport_drop_is_twenty_percent_without_changing_total_weight(tmp_path):
    from collections import Counter
    from spy_game.rewards import RewardResolver
    from test_spy_game import settings

    config = settings(tmp_path)
    total = sum(entry.weight for entry in config.dead_drop_entries)
    assert total == 100
    outcomes = Counter(
        RewardResolver(config).resolve_dead_drop(FixedRandom(roll)).reward_id
        for roll in range(1, total + 1)
    )
    assert outcomes["fake_passport"] == 20 and outcomes[None] == 5


@pytest.mark.asyncio
async def test_upgrade_keeps_legacy_chase_starter_and_original_deadline(tmp_path):
    service = await initialized_service(tmp_path)
    event = await chase(service)
    await service.get_profile(user_id=1, username="legacy", display_name=None, now=NOW)

    def legacy(connection):
        connection.execute("DROP TABLE chase_rounds")
        connection.execute("DELETE FROM schema_migrations WHERE version IN (15,16)")
        connection.execute("DROP TABLE item_durability")
        connection.execute("DROP TABLE equipment_uses")
        connection.execute("ALTER TABLE intercept_game_runs DROP COLUMN reward_id")
        connection.execute("ALTER TABLE intercept_game_runs DROP COLUMN reward_amount")
        connection.execute(
            "INSERT INTO event_participants(event_id,user_id,status,payload_json,created_at,updated_at) VALUES (?,1,'pending','{}',?,?)",
            (event, NOW.isoformat(), NOW.isoformat()),
        )
        connection.execute(
            "UPDATE game_events SET payload_json=? WHERE id=?",
            ('{"action":"chase","config_id":"two_stage_v1","manual":true}', event),
        )

    await service.database.transaction(legacy, immediate=True)
    config = service.settings
    await service.close()
    service = SpyGameService(config, rng=FixedRandom())
    try:
        await service.initialize(now=NOW + timedelta(seconds=2))
        result = await service.get_chase(event)
        assert result.leader_user_id == 1 and result.turn == 1
        assert result.deadline == NOW + timedelta(seconds=60)
        assert (
            await press(service, event, seconds=3)
        ).status is ChaseStatus.ALREADY_LEADING
        await service.settle_chases(now=NOW + timedelta(seconds=60))
        assert (await service.get_agents(1))[0].amount == 1
    finally:
        await service.close()
