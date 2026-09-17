import asyncio
import json
from datetime import timedelta

import pytest
import pytest_asyncio

from spy_game.equipment import EQUIPMENT_EFFECTS
from spy_game.models import ClaimStatus, EquipmentStatus, FindMoleGameStatus, NpcStatus
from spy_game.service import SpyGameService
from test_spy_game import (
    CHAT_ID,
    NOW,
    FixedRandom,
    EndRandom,
    initialized_service,
    grant_items,
    grant_agents,
)
from test_spy_chase import press


@pytest_asyncio.fixture
async def service(tmp_path):
    game = await initialized_service(tmp_path)
    yield game
    await game.close()


async def equip(service, item, count=1, uid=1):
    await grant_items(service, uid, {item: count})
    result = await service.equip_item(chat_id=CHAT_ID, user_id=uid, item_type=item)
    assert result.status is EquipmentStatus.SUCCESS
    return result.slot


async def holding(service, item, uid=1):
    return next(
        (x for x in (await service.get_inventory(uid)).items if x.item_type == item),
        None,
    )


async def spawn(service, event_type):
    event = (await service.manual_spawn(CHAT_ID, event_type=event_type, now=NOW)).event
    assert event
    await service.attach_message(event.event_id, int(event.event_id[:8], 16))
    return event


async def recruit(service):
    event = await spawn(service, "recruitment")
    args = dict(
        event_id=event.event_id,
        action="claim",
        chat_id=CHAT_ID,
        user_id=1,
        username=None,
        display_name=None,
        now=NOW,
    )
    result = await service.claim_event(**args)
    return event, result, args


async def exchange(service, recipe, key="exchange-1"):
    return await service.exchange_with_contact(
        chat_id=CHAT_ID,
        user_id=1,
        username=None,
        display_name=None,
        recipe_id=recipe,
        operation_id=key,
        now=NOW,
    )


@pytest.mark.asyncio
async def test_all_six_items_can_be_equipped_but_share_three_slots(service):
    for item in EQUIPMENT_EFFECTS:
        await grant_items(service, 1, {item: 1})
    for index, item in enumerate(EQUIPMENT_EFFECTS):
        result = await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type=item)
        assert result.status is (
            EquipmentStatus.SUCCESS if index < 3 else EquipmentStatus.NO_FREE_SLOT
        )
    for slot in (1, 2, 3):
        await service.unequip_item(chat_id=CHAT_ID, user_id=1, slot=slot)
    for item in ("intel_file", "satellite_image", "access_code"):
        result = await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type=item)
        assert result.status is EquipmentStatus.SUCCESS


@pytest.mark.asyncio
async def test_wiretap_wears_only_on_bonus_duplicate_does_not_spend_and_last_use_breaks(
    service,
):
    await equip(service, "wiretap", count=2)
    service.repository.context.rng = EndRandom()
    event, result, _ = await recruit(service)
    assert result.reward.amount == 1
    assert (await holding(service, "wiretap")).uses_remaining == 5
    await service.cancel_publication(event.event_id, now=NOW)
    service.repository.context.rng = FixedRandom()
    for uses in range(5):
        event, result, args = await recruit(service)
        assert result.reward.amount == 2
        assert (await service.claim_event(**args)).status is ClaimStatus.ALREADY_CLAIMED
        item = await holding(service, "wiretap")
        assert item.uses_remaining == (4 - uses if uses < 4 else None)
        await service.cancel_publication(event.event_id, now=NOW)
    item = await holding(service, "wiretap")
    assert item.amount == 1 and item.exchangeable_amount == 1
    assert not (await service.get_inventory(1)).equipped
    # The spare is not silently equipped; a new action must opt into consuming it.
    event, result, _ = await recruit(service)
    assert result.reward.amount == 1


@pytest.mark.asyncio
async def test_used_item_cannot_be_exchanged_and_new_copies_are_separate(service):
    slot = await equip(service, "wiretap", count=2)
    await grant_items(service, 1, {"radio": 2})
    event, _, _ = await recruit(service)
    await service.unequip_item(chat_id=CHAT_ID, user_id=1, slot=slot)
    item = await holding(service, "wiretap")
    assert (item.amount, item.exchangeable_amount, item.uses_remaining) == (2, 1, 4)
    first, repeated = await asyncio.gather(
        exchange(service, "counter_surveillance"),
        exchange(service, "counter_surveillance"),
    )
    assert first.status is repeated.status is NpcStatus.SUCCESS
    assert (await holding(service, "wiretap")).amount == 1
    assert (await holding(service, "satellite_image")).amount == 1
    assert (
        await exchange(service, "counter_surveillance", "next-exchange")
    ).status is NpcStatus.INSUFFICIENT_RESOURCES
    assert (await holding(service, "radio")).amount == 1
    # Re-equipping continues the used specimen, not a fresh one.
    await grant_items(service, 1, {"wiretap": 2})
    await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="wiretap")
    assert (await holding(service, "wiretap")).uses_remaining == 4
    assert (await holding(service, "wiretap")).exchangeable_amount == 1


@pytest.mark.asyncio
async def test_equipped_pristine_is_reserved_until_unequipped(service):
    slot = await equip(service, "fake_passport")
    await grant_agents(service, 1, {"operative": 1})
    assert (
        await exchange(service, "chief_illegal")
    ).status is NpcStatus.INSUFFICIENT_RESOURCES
    await service.unequip_item(chat_id=CHAT_ID, user_id=1, slot=slot)
    assert (await exchange(service, "chief_illegal")).status is NpcStatus.SUCCESS
    assert await holding(service, "fake_passport") is None


@pytest.mark.asyncio
async def test_passport_shortens_only_accepted_turns_and_breaks_after_three(service):
    await equip(service, "fake_passport")
    event = await spawn(service, "chase")
    for turn in range(1, 7):
        result = await press(
            service, event.event_id, user=1 if turn % 2 else 2, seconds=turn
        )
        assert result.hold_seconds == (24, 23, 16, 15, 8, 5)[turn - 1]
        assert result.deadline == NOW + timedelta(seconds=turn + result.hold_seconds)
        if turn % 2:
            await press(service, event.event_id, user=1, seconds=turn)
    assert await holding(service, "fake_passport") is None
    uses = await service.database.read(
        lambda c: c.execute(
            "SELECT COUNT(*) FROM equipment_uses WHERE item_type='fake_passport'"
        ).fetchone()[0]
    )
    assert uses == 3


@pytest.mark.asyncio
async def test_radio_bonus_is_personal_and_only_paid_on_completion(service):
    await equip(service, "radio")
    for _ in range(3):
        event = await spawn(service, "cooperative_operation")
        for uid in (1, 2, 3):
            result = await service.contribute_cooperative(
                event_id=event.event_id,
                chat_id=CHAT_ID,
                user_id=uid,
                username=None,
                display_name=None,
                now=NOW,
            )
            if uid < 3:
                assert not result.radio_bonus_user_ids
        assert result.radio_bonus_user_ids == (1,)
    assert (await service.get_agents(1))[0].amount == 9
    assert (await service.get_agents(2))[0].amount == 6
    assert await holding(service, "radio") is None


@pytest.mark.asyncio
async def test_intel_file_boosts_mole_win_but_not_failed_accusation(service):
    await equip(service, "intel_file")
    event = await spawn(service, "find_mole")
    wrong = next(
        s.id
        for s in event.mole_case.suspects
        if s.id != event.mole_case.correct_suspect_id
    )
    args = dict(chat_id=CHAT_ID, user_id=1, username=None, display_name=None, now=NOW)
    result = await service.accuse_find_mole_event(
        event_id=event.event_id, suspect_id=wrong, idempotency_key="wrong-mole", **args
    )
    assert result.status is FindMoleGameStatus.FAILED
    assert (await holding(service, "intel_file")).uses_remaining == 3
    await service.cancel_publication(event.event_id, now=NOW)
    for _ in range(3):
        event = await spawn(service, "find_mole")
        kw = dict(
            event_id=event.event_id,
            suspect_id=event.mole_case.correct_suspect_id,
            idempotency_key="good-mole",
            **args
        )
        won = await service.accuse_find_mole_event(**kw)
        assert won.agent_reward.amount == 5
        replay = await service.accuse_find_mole_event(**kw)
        assert replay.status is FindMoleGameStatus.ALREADY_RESOLVED
    assert await holding(service, "intel_file") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_satellite_modifies_inline_and_html5_loot_and_preserves_replay(
    service, empty
):
    await equip(service, "satellite_image")
    service.repository.context.rng = EndRandom() if empty else FixedRandom()
    event = await spawn(service, "dead_drop")
    reward = (
        await service.search_dead_drop(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name=None,
            now=NOW,
        )
    ).reward
    assert reward.reward_id == "intel_file" and reward.amount == (1 if empty else 2)
    event = await spawn(service, "dead_drop")
    run = await service.start_dead_drop_game(
        chat_id=CHAT_ID,
        message_id=int(event.event_id[:8], 16),
        user_id=1,
        username=None,
        display_name=None,
        now=NOW,
    )
    code = await service.database.read(
        lambda c: json.loads(
            c.execute(
                "SELECT code_json FROM dead_drop_game_runs WHERE id=?", (run.run_id,)
            ).fetchone()[0]
        )
    )
    won = await service.guess_dead_drop_game(run.launch_token, tuple(code), now=NOW)
    assert won.reward == reward
    replay = await service.guess_dead_drop_game(run.launch_token, tuple(code), now=NOW)
    assert replay.reward == reward
    assert (await holding(service, "satellite_image")).uses_remaining == 1


@pytest.mark.asyncio
async def test_wear_and_bonus_rollback_together(service, monkeypatch):
    await equip(service, "satellite_image")
    event = await spawn(service, "dead_drop")
    original = service.repository.economy.equipment.consume

    def failing(*args):
        original(*args)
        raise RuntimeError("after wear")

    monkeypatch.setattr(service.repository.economy.equipment, "consume", failing)
    with pytest.raises(RuntimeError):
        await service.search_dead_drop(
            event_id=event.event_id,
            chat_id=CHAT_ID,
            user_id=1,
            username=None,
            display_name=None,
            now=NOW,
        )
    assert (await holding(service, "satellite_image")).uses_remaining == 3
    assert await holding(service, "intel_file") is None
    assert (await service.get_chat_status(CHAT_ID)).active_event_id == event.event_id
    assert (
        await service.database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM equipment_uses").fetchone()[0]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_unequipped_used_specimen_survives_restart(tmp_path):
    service = await initialized_service(tmp_path)
    slot = await equip(service, "wiretap")
    await recruit(service)
    await service.unequip_item(chat_id=CHAT_ID, user_id=1, slot=slot)
    config = service.settings
    await service.close()
    service = SpyGameService(config, rng=FixedRandom())
    try:
        await service.initialize(now=NOW)
        await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="wiretap")
        item = await holding(service, "wiretap")
        assert item.uses_remaining == 4 and item.exchangeable_amount == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_access_code_modifies_both_intercept_modes_and_persists_final_reward(
    service,
):
    await equip(service, "access_code")
    event = await spawn(service, "intercept")
    scenario = service.settings.intercept_scenario(event.config_id)
    wrong = next(
        option.id
        for option in scenario.options
        if option.id != scenario.correct_option_id
    )
    await service.answer_intercept(
        event_id=event.event_id,
        choice_id=wrong,
        chat_id=CHAT_ID,
        user_id=1,
        username=None,
        display_name=None,
        now=NOW,
    )
    assert (await holding(service, "access_code")).uses_remaining == 3
    for use in range(3):
        event = await spawn(service, "intercept")
        if use == 0:
            result = await service.answer_intercept(
                event_id=event.event_id,
                choice_id=scenario.correct_option_id,
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name=None,
                now=NOW,
            )
            assert result.reward.amount == scenario.reward_amount + 1
        else:
            run = await service.start_intercept_game(
                chat_id=CHAT_ID,
                message_id=int(event.event_id[:8], 16),
                user_id=1,
                username=None,
                display_name=None,
                now=NOW,
            )
            result = await service.finish_intercept_game(
                run.launch_token, run.targets, now=NOW
            )
            assert result.reward.amount == scenario.reward_amount + 1
            for _ in range(2):
                replay = await service.finish_intercept_game(
                    run.launch_token, run.targets, now=NOW
                )
                reopened = await service.get_intercept_game(run.launch_token, now=NOW)
                assert replay.reward == reopened.reward == result.reward
    # This scenario also awards access codes: the original is destroyed, new copies remain fresh.
    item = await holding(service, "access_code")
    assert item.amount == item.exchangeable_amount == 6
    assert item.uses_remaining is None and not (await service.get_inventory(1)).equipped


@pytest.mark.asyncio
async def test_concurrent_wiretap_claims_consume_one_charge(service):
    await equip(service, "wiretap")
    event = await spawn(service, "recruitment")
    results = await asyncio.gather(
        *(
            service.claim_event(
                event_id=event.event_id,
                action="claim",
                chat_id=CHAT_ID,
                user_id=1,
                username=None,
                display_name=None,
                now=NOW,
            )
            for _ in range(10)
        )
    )
    assert sum(r.status is ClaimStatus.WON for r in results) == 1
    assert (await holding(service, "wiretap")).uses_remaining == 4
    assert (await service.get_agents(1))[0].amount == 2


@pytest.mark.asyncio
async def test_equip_exchange_race_cannot_use_reserved_copy_twice(service):
    await grant_items(service, 1, {"fake_passport": 1})
    await grant_agents(service, 1, {"operative": 1})
    dressed, traded = await asyncio.gather(
        service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="fake_passport"),
        exchange(service, "chief_illegal"),
    )
    assert (dressed.status, traded.status) in {
        (EquipmentStatus.SUCCESS, NpcStatus.INSUFFICIENT_RESOURCES),
        (EquipmentStatus.NOT_OWNED, NpcStatus.SUCCESS),
    }


@pytest.mark.asyncio
async def test_version15_upgrade_initializes_wear_cleans_orphan_and_restores_rewards(
    tmp_path,
):
    service = await initialized_service(tmp_path)
    await grant_items(service, 1, {"wiretap": 2})
    await service.equip_item(chat_id=CHAT_ID, user_id=1, item_type="wiretap")
    event = await spawn(service, "intercept")
    run = await service.start_intercept_game(
        chat_id=CHAT_ID,
        message_id=int(event.event_id[:8], 16),
        user_id=1,
        username=None,
        display_name=None,
        now=NOW,
    )
    won = await service.finish_intercept_game(run.launch_token, run.targets, now=NOW)

    def legacy(c):
        c.execute(
            "INSERT INTO equipped_items(user_id,slot,item_type) VALUES (1,2,'radio')"
        )
        c.execute("DROP TABLE equipment_uses")
        c.execute("DROP TABLE item_durability")
        c.execute("ALTER TABLE chase_rounds DROP COLUMN hold_seconds")
        c.execute("ALTER TABLE intercept_game_runs DROP COLUMN reward_id")
        c.execute("ALTER TABLE intercept_game_runs DROP COLUMN reward_amount")
        c.execute("DELETE FROM schema_migrations WHERE version=16")

    await service.database.transaction(legacy, immediate=True)
    config = service.settings
    await service.close()
    service = SpyGameService(config, rng=FixedRandom())
    try:
        await service.initialize(now=NOW)
        item = await holding(service, "wiretap")
        assert item.uses_remaining == 5 and item.exchangeable_amount == 1
        assert [i.item_type for i in (await service.get_inventory(1)).equipped] == [
            "wiretap"
        ]
        restored = await service.get_intercept_game(run.launch_token, now=NOW)
        assert restored.reward == won.reward
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_inventory_text_explains_effect_and_separate_exchange_balance(service):
    from handlers.spy.menu_views import build_inventory_text

    await equip(service, "wiretap", count=2)
    await recruit(service)
    text = build_inventory_text(await service.get_inventory(1))
    assert "4/5" in text and "Для обмена: 1" in text
    assert "20%" in text and "нельзя обменять" in text
