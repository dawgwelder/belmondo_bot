"""Regression coverage for reputation cycles separated by agency founding."""

import asyncio
import json

import pytest

from spy_game.models import AgencyStatus, EconomyStatus
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_history", [False, True])
async def test_reputation_after_agency_reset_preserves_history_and_spends_once(
    tmp_path, legacy_history
):
    settings = SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "progression.sqlite3",
        allowed_chat_ids=frozenset({-100123}),
    )
    service = SpyGameService(settings)
    identity = dict(user_id=1, username="bond", display_name="Bond")
    action = dict(chat_id=-100123, **identity)
    await service.initialize()
    try:
        await service.enable_chat(-100123)
        await service.get_profile(**identity)

        async def fund(costs):
            # Leave a surplus so a duplicate cannot hide behind insufficient funds.
            await service.database.transaction(
                lambda connection: connection.executemany(
                    "INSERT INTO user_agents(user_id, agent_type, amount) VALUES (1, ?, ?) "
                    "ON CONFLICT(user_id, agent_type) DO UPDATE SET amount=excluded.amount",
                    [(cost.agent_type, cost.amount * 3) for cost in costs],
                ),
                immediate=True,
            )

        async def history():
            return await service.database.read(
                lambda connection: [
                    tuple(row)
                    for row in connection.execute(
                        "SELECT idempotency_key, metadata_json FROM economy_history "
                        "WHERE user_id=1 AND action='prestige' ORDER BY id"
                    )
                ]
            )

        previous_history = []
        for level in range(3):
            target = settings.agency_reputation_requirement(level)
            for reputation in range(target):
                costs = settings.prestige_costs(reputation)
                await fund(costs)
                results = await asyncio.gather(
                    *(
                        service.increase_reputation(
                            **action, expected_reputation=reputation
                        )
                        for _ in range(2)
                    ),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result
                assert sorted(result.status.value for result in results) == sorted(
                    [EconomyStatus.SUCCESS.value, EconomyStatus.STALE.value]
                )
                balances = {
                    holding.agent_type: holding.amount
                    for holding in await service.get_agents(1)
                }
                for cost in costs:
                    assert balances[cost.agent_type] == cost.amount * 2

            if level == 0 and legacy_history:
                # Simulate rows written before the fix, without deleting history.
                for key, metadata in await history():
                    old_reputation = json.loads(metadata)["from"]
                    await service.database.transaction(
                        lambda connection: connection.execute(
                            "UPDATE economy_history SET idempotency_key=? "
                            "WHERE idempotency_key=?",
                            (f"prestige:1:{old_reputation}", key),
                        ),
                        immediate=True,
                    )

            rows = await history()
            assert rows[: len(previous_history)] == previous_history
            assert len(rows) == len(previous_history) + target
            previous_history = rows
            profile = await service.get_profile(**identity)
            assert (profile.agency_level, profile.reputation) == (level, target)
            if level < 2:
                await fund(settings.agency_requirements(level))
                result = await service.found_agency(
                    **action, expected_agency_level=level
                )
                assert result.status is AgencyStatus.SUCCESS
                profile = await service.get_profile(**identity)
                assert (profile.agency_level, profile.reputation) == (level + 1, 0)
    finally:
        await service.close()
