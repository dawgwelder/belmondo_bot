import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

import pytest

from spy_game.models import ChaseStatus, DuelWagerStatus
from spy_game.service import SpyGameService
from test_spy_chase import chase, press
from test_spy_duels import NOW as DUEL_NOW
from test_spy_duels import balance, create_wager, service_with_agents
from test_spy_game import CHAT_ID, NOW, FixedRandom, initialized_service


def restore_legacy_utc_timestamps(path):
    legacy_zone = datetime.fromisoformat("2026-01-01T00:00:00+00:00").tzinfo
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.create_function(
            "legacy_utc",
            1,
            lambda value: (
                None
                if value is None
                else datetime.fromisoformat(value).astimezone(legacy_zone).isoformat()
            ),
        )
        for table, columns in (
            ("game_events", ("created_at", "expires_at", "resolved_at")),
            ("chase_rounds", ("deadline", "notified_at")),
            ("spy_duels", ("created_at", "expires_at", "accepted_at", "resolved_at")),
        ):
            assignments = ", ".join(f"{name}=legacy_utc({name})" for name in columns)
            connection.execute(f"UPDATE {table} SET {assignments}")
        connection.execute("DELETE FROM schema_migrations WHERE version=21")


@pytest.mark.asyncio
async def test_legacy_chase_keeps_countdown_and_pays_once_at_exact_deadline(tmp_path):
    service = await initialized_service(tmp_path)
    config = service.settings
    try:
        event_id = await chase(service)
        started = await press(service, event_id)
        deadline = started.deadline
        assert deadline == NOW + timedelta(seconds=30)
    finally:
        await service.close()
    restore_legacy_utc_timestamps(config.database_path)

    restored = SpyGameService(config, rng=FixedRandom())
    try:
        restart_time = NOW + timedelta(seconds=15)
        await restored.initialize(now=restart_time)
        active = await restored.get_chase(event_id)
        assert active.status is ChaseStatus.STARTED
        assert active.deadline == deadline
        assert (active.deadline - restart_time).total_seconds() == 15
        chat = await restored.get_chat_status(CHAT_ID)
        assert chat.active_event_id == event_id
        assert chat.active_event_expires_at == deadline
        assert await restored.get_agents(1) == ()

        assert (
            await restored.settle_chases(now=deadline - timedelta(microseconds=1)) == ()
        )
        settled = await restored.settle_chases(now=deadline)
        assert len(settled) == 1
        assert settled[0].status is ChaseStatus.COMPLETED
        assert settled[0].leader_user_id == 1
        assert [
            (item.agent_type, item.amount) for item in await restored.get_agents(1)
        ] == [("informant", 1)]
        await restored.settle_chases(now=deadline + timedelta(seconds=1))
        assert (await restored.get_agents(1))[0].amount == 1
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True], ids=["pending", "choosing"])
async def test_legacy_duel_keeps_escrow_until_exact_deadline(tmp_path, accepted):
    service = await service_with_agents(tmp_path)
    config = service.settings
    try:
        wager = await create_wager(service)
        if accepted:
            wager = await service.accept_duel_wager(
                duel_id=wager.duel_id,
                user_id=2,
                username="opponent",
                display_name="Opponent",
                now=DUEL_NOW + timedelta(seconds=1),
            )
        deadline = wager.expires_at
        status = wager.status
        assert status is (
            DuelWagerStatus.CHOOSING if accepted else DuelWagerStatus.PENDING
        )
    finally:
        await service.close()
    restore_legacy_utc_timestamps(config.database_path)

    restored = SpyGameService(config, rng=FixedRandom())
    try:
        restart_time = DUEL_NOW + timedelta(seconds=30)
        await restored.initialize(now=restart_time)
        active = await restored.get_duel_wager(wager.duel_id, now=restart_time)
        assert active.status is status
        assert active.expires_at == deadline
        assert await balance(restored, 1) == 7
        assert await balance(restored, 2) == (5 if accepted else 8)

        before = await restored.expire_duel_wager(
            wager.duel_id, now=deadline - timedelta(microseconds=1)
        )
        assert before.status is status
        assert await balance(restored, 1) == 7
        assert await balance(restored, 2) == (5 if accepted else 8)

        expired = await restored.expire_duel_wager(wager.duel_id, now=deadline)
        assert expired.status is DuelWagerStatus.REFUNDED
        assert expired.resolution == (
            "move_timeout_no_moves" if accepted else "accept_timeout"
        )
        await restored.expire_duel_wager(
            wager.duel_id, now=deadline + timedelta(seconds=1)
        )
        assert await balance(restored, 1) == 10
        assert await balance(restored, 2) == 8
        history_count = await restored.database.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) FROM spy_duel_history WHERE duel_id=?",
                (wager.duel_id,),
            ).fetchone()[0]
        )
        assert history_count == 1
    finally:
        await restored.close()
