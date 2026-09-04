import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import handlers.duel as duel_handlers
from spy_game.duels import DUEL_BEATS
from spy_game.models import DuelWagerStatus
from spy_game.service import SpyGameService
from spy_game.settings import SpySettings


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
CHAT_ID = -100987
SCENARIO = {
    "title": "Операция «Эскроу»",
    "setting": "Два агента встретились на крыше.",
    "condition": "Побеждает лучший манёвр.",
}


class FixedRandom:
    def randint(self, start, end):
        return start


def settings(tmp_path):
    return SpySettings(
        mode="dev",
        enabled=True,
        database_path=tmp_path / "spy-duels.sqlite3",
        allowed_chat_ids=frozenset({CHAT_ID}),
        activity_user_debounce_seconds=0,
    )


async def service_with_agents(tmp_path, challenger=10, opponent=8):
    service = SpyGameService(settings(tmp_path), rng=FixedRandom())
    await service.initialize(now=NOW)
    await service.enable_chat(CHAT_ID, now=NOW)
    for user_id, username, amount in (
        (1, "challenger", challenger),
        (2, "opponent", opponent),
    ):
        await service.get_profile(
            user_id=user_id,
            username=username,
            display_name=f"Private {username}",
            now=NOW,
        )
        await service.database.transaction(
            lambda connection, user_id=user_id, amount=amount: connection.execute(
                "INSERT INTO user_agents(user_id, agent_type, amount) "
                "VALUES (?, 'informant', ?)",
                (user_id, amount),
            ),
            immediate=True,
        )
    return service


async def create_wager(service, duel_id="wager1", now=NOW):
    return await service.create_duel_wager(
        duel_id=duel_id,
        chat_id=CHAT_ID,
        challenger_user_id=1,
        challenger_username="challenger",
        challenger_display_name="Private Challenger",
        opponent_user_id=2,
        opponent_username="opponent",
        opponent_display_name="Private Opponent",
        stake_amount=3,
        scenario=SCENARIO,
        now=now,
    )


async def balance(service, user_id):
    holdings = await service.get_agents(user_id)
    return next(
        (item.amount for item in holdings if item.agent_type == "informant"),
        0,
    )


@pytest.mark.asyncio
async def test_wagered_duel_escrows_and_settles_pot_once(tmp_path):
    service = await service_with_agents(tmp_path)
    try:
        created = await create_wager(service)
        assert created.status is DuelWagerStatus.PENDING
        assert created.challenger_name == "@challenger"
        assert "Private" not in created.challenger_name
        assert await balance(service, 1) == 7

        accepted = await service.accept_duel_wager(
            duel_id=created.duel_id,
            user_id=2,
            username="opponent",
            display_name="Private Opponent",
            now=NOW + timedelta(seconds=1),
        )
        assert accepted.status is DuelWagerStatus.CHOOSING
        assert await balance(service, 2) == 5

        first = await service.choose_duel_move(
            duel_id=created.duel_id,
            user_id=1,
            action="attack",
            now=NOW + timedelta(seconds=2),
        )
        assert first.status is DuelWagerStatus.CHOOSING
        won = await service.choose_duel_move(
            duel_id=created.duel_id,
            user_id=2,
            action="environment",
            now=NOW + timedelta(seconds=3),
        )
        assert won.status is DuelWagerStatus.WON
        assert won.winner_user_id == 1
        assert won.winner_name == "@challenger"
        assert await balance(service, 1) == 13
        assert await balance(service, 2) == 5

        repeated = await service.choose_duel_move(
            duel_id=created.duel_id,
            user_id=2,
            action="environment",
            now=NOW + timedelta(seconds=4),
        )
        assert repeated.status is DuelWagerStatus.WON
        assert await balance(service, 1) == 13
        history = await service.database.read(
            lambda connection: connection.execute(
                "SELECT outcome, pot_amount, COUNT(*) FROM spy_duel_history "
                "WHERE duel_id = ?",
                (created.duel_id,),
            ).fetchone()
        )
        assert tuple(history) == ("moves", 6, 1)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_wager_requires_enabled_spy_chat(tmp_path):
    service = SpyGameService(settings(tmp_path), rng=FixedRandom())
    await service.initialize(now=NOW)
    try:
        result = await service.create_duel_wager(
            duel_id="disabled-chat",
            chat_id=CHAT_ID,
            challenger_user_id=1,
            challenger_username="challenger",
            challenger_display_name="Private Challenger",
            opponent_user_id=2,
            opponent_username="opponent",
            opponent_display_name="Private Opponent",
            stake_amount=3,
            scenario=SCENARIO,
            now=NOW,
        )

        assert result.status is DuelWagerStatus.DISABLED
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_accept_requires_matching_opponent_and_available_stake(tmp_path):
    service = await service_with_agents(tmp_path, opponent=2)
    try:
        created = await create_wager(service)
        outsider = await service.accept_duel_wager(
            duel_id=created.duel_id,
            user_id=3,
            username="outsider",
            display_name="Outsider",
            now=NOW + timedelta(seconds=1),
        )
        assert outsider.status is DuelWagerStatus.INVALID_PARTICIPANT

        insufficient = await service.accept_duel_wager(
            duel_id=created.duel_id,
            user_id=2,
            username="opponent",
            display_name="Opponent",
            now=NOW + timedelta(seconds=2),
        )
        assert insufficient.status is DuelWagerStatus.INSUFFICIENT_AGENTS
        assert await balance(service, 2) == 2
        assert await balance(service, 1) == 7

        declined = await service.close_pending_duel_wager(
            duel_id=created.duel_id,
            user_id=2,
            username="opponent",
            action="decline",
            now=NOW + timedelta(seconds=3),
        )
        assert declined.status is DuelWagerStatus.REFUNDED
        assert await balance(service, 1) == 10
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_move_timeout_refunds_or_awards_technical_win(tmp_path):
    service = await service_with_agents(tmp_path)
    try:
        no_moves = await create_wager(service, "no_moves")
        await service.accept_duel_wager(
            duel_id=no_moves.duel_id,
            user_id=2,
            username="opponent",
            display_name="Opponent",
            now=NOW + timedelta(seconds=1),
        )
        refunded = await service.expire_duel_wager(
            no_moves.duel_id,
            now=NOW + timedelta(seconds=182),
        )
        assert refunded.status is DuelWagerStatus.REFUNDED
        assert refunded.resolution == "move_timeout_no_moves"
        assert await balance(service, 1) == 10
        assert await balance(service, 2) == 8

        one_move = await create_wager(
            service,
            "one_move",
            now=NOW + timedelta(seconds=183),
        )
        await service.accept_duel_wager(
            duel_id=one_move.duel_id,
            user_id=2,
            username="opponent",
            display_name="Opponent",
            now=NOW + timedelta(seconds=184),
        )
        await service.choose_duel_move(
            duel_id=one_move.duel_id,
            user_id=2,
            action="risk",
            now=NOW + timedelta(seconds=185),
        )
        technical = await service.expire_duel_wager(
            one_move.duel_id,
            now=NOW + timedelta(seconds=365),
        )
        assert technical.status is DuelWagerStatus.WON
        assert technical.winner_user_id == 2
        assert technical.resolution == "move_timeout"
        assert await balance(service, 1) == 7
        assert await balance(service, 2) == 11
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_forfeit_awards_pot_and_master_cancel_refunds_both_stakes(tmp_path):
    service = await service_with_agents(tmp_path)
    try:
        first = await create_wager(service)
        await service.accept_duel_wager(
            duel_id=first.duel_id,
            user_id=2,
            username="opponent",
            display_name="Private Opponent",
            now=NOW + timedelta(seconds=1),
        )
        forfeited = await service.forfeit_duel_wager(
            first.duel_id,
            1,
            now=NOW + timedelta(seconds=2),
        )
        assert forfeited.status is DuelWagerStatus.WON
        assert forfeited.winner_user_id == 2
        assert forfeited.resolution == "forfeit"
        assert await balance(service, 1) == 7
        assert await balance(service, 2) == 11

        repeated = await service.forfeit_duel_wager(
            first.duel_id,
            1,
            now=NOW + timedelta(seconds=3),
        )
        assert repeated.status is DuelWagerStatus.WON
        assert await balance(service, 2) == 11

        second = await create_wager(
            service,
            duel_id="master-refund",
            now=NOW + timedelta(seconds=4),
        )
        await service.accept_duel_wager(
            duel_id=second.duel_id,
            user_id=2,
            username="opponent",
            display_name="Private Opponent",
            now=NOW + timedelta(seconds=5),
        )
        cancelled = await service.cancel_duel_wager_as_master(
            second.duel_id,
            now=NOW + timedelta(seconds=6),
        )
        assert cancelled.status is DuelWagerStatus.REFUNDED
        assert cancelled.resolution == "master_cancel"
        assert await balance(service, 1) == 7
        assert await balance(service, 2) == 11
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_pending_stake_is_refunded_during_restart_reconciliation(tmp_path):
    config = settings(tmp_path)
    first = await service_with_agents(tmp_path)
    created = await create_wager(first)
    assert await balance(first, 1) == 7
    await first.close()

    second = SpyGameService(config, rng=FixedRandom())
    await second.initialize(now=NOW + timedelta(seconds=121))
    try:
        restored = await second.get_duel_wager(
            created.duel_id,
            now=NOW + timedelta(seconds=121),
        )
        assert restored.status is DuelWagerStatus.REFUNDED
        assert restored.resolution == "accept_timeout"
        assert await balance(second, 1) == 10
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_concurrent_accept_and_moves_never_double_debit_or_pot(tmp_path):
    service = await service_with_agents(tmp_path)
    try:
        created = await create_wager(service)
        accepts = await asyncio.gather(
            *(
                service.accept_duel_wager(
                    duel_id=created.duel_id,
                    user_id=2,
                    username="opponent",
                    display_name="Opponent",
                    now=NOW + timedelta(seconds=1),
                )
                for _ in range(2)
            )
        )
        assert {result.status for result in accepts} == {
            DuelWagerStatus.CHOOSING,
            DuelWagerStatus.INVALID_PARTICIPANT,
        }
        assert await balance(service, 2) == 5

        results = await asyncio.gather(
            service.choose_duel_move(
                duel_id=created.duel_id,
                user_id=1,
                action="defend",
                now=NOW + timedelta(seconds=2),
            ),
            service.choose_duel_move(
                duel_id=created.duel_id,
                user_id=2,
                action="attack",
                now=NOW + timedelta(seconds=2),
            ),
        )
        assert DuelWagerStatus.WON in {result.status for result in results}
        assert await balance(service, 1) == 13
        assert await balance(service, 2) == 5
    finally:
        await service.close()


def test_every_duel_action_beats_two_and_loses_to_two():
    assert all(len(beaten) == 2 for beaten in DUEL_BEATS.values())
    for action, beaten in DUEL_BEATS.items():
        assert action not in beaten
        assert all(action not in DUEL_BEATS[loser] for loser in beaten)


def test_duel_stake_argument_is_optional_and_explicit():
    assert duel_handlers._requested_stake(SimpleNamespace(args=["@bond"])) == 1
    assert duel_handlers._requested_stake(SimpleNamespace(args=[])) == 1
    assert duel_handlers._requested_stake(SimpleNamespace(args=["@bond", "3"])) == 3
    assert duel_handlers._requested_stake(SimpleNamespace(args=["3"])) == 3


@pytest.mark.asyncio
async def test_telegram_duel_callbacks_settle_wager_through_spy_service(
    tmp_path,
    monkeypatch,
):
    service = await service_with_agents(tmp_path)
    try:
        wager = await create_wager(service, now=datetime.now(timezone.utc))
        await service.attach_duel_message(wager.duel_id, 900)
        duel_data = duel_handlers._duel_data_from_wager(wager)
        duel_data["timeout_job_name"] = f"duel-accept-timeout:{CHAT_ID}:wager1"
        job_queue = SimpleNamespace(
            get_jobs_by_name=lambda name: (),
            run_once=Mock(),
        )
        context = SimpleNamespace(
            chat_data={"duels": {wager.duel_id: duel_data}},
            bot_data={"spy_game": service, "master": 999},
            job_queue=job_queue,
        )

        async def click(user_id, username, data):
            query = SimpleNamespace(
                data=data,
                answer=AsyncMock(),
                edit_message_text=AsyncMock(),
                message=SimpleNamespace(message_id=900),
            )
            update = SimpleNamespace(
                callback_query=query,
                effective_user=SimpleNamespace(
                    id=user_id,
                    username=username,
                    full_name=f"Private {username}",
                ),
                effective_chat=SimpleNamespace(id=CHAT_ID),
            )
            await duel_handlers.duel_callback(update, context)
            return query

        await click(2, "opponent", "duel:wager1:accept")
        assert await balance(service, 1) == 7
        assert await balance(service, 2) == 5
        assert job_queue.run_once.call_args.args[0] is duel_handlers.duel_move_timeout

        await click(1, "challenger", "duel:wager1:move:defend")
        narrator = AsyncMock(
            return_value={
                "winner": "challenger",
                "narration": "Сервер уже определил победителя.",
                "reason": "Защита остановила атаку.",
            }
        )
        monkeypatch.setattr(duel_handlers, "_narrate_wagered_duel", narrator)
        result_query = await click(2, "opponent", "duel:wager1:move:attack")

        assert await balance(service, 1) == 13
        assert await balance(service, 2) == 5
        assert context.chat_data["duels"] == {}
        final_text = result_query.edit_message_text.await_args.args[0]
        assert "Победитель: @challenger" in final_text
        assert "Осведомитель ×6" in final_text
        assert "Private" not in final_text
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_duel_command_defaults_to_one_agent_wager(tmp_path, monkeypatch):
    service = await service_with_agents(tmp_path)
    challenger = SimpleNamespace(
        id=1,
        username="challenger",
        full_name="Private Challenger",
        is_bot=False,
    )
    opponent = SimpleNamespace(
        id=2,
        username="opponent",
        full_name="Private Opponent",
        is_bot=False,
    )
    message = SimpleNamespace(
        message_id=50,
        reply_to_message=SimpleNamespace(from_user=opponent),
        entities=(),
        reply_text=AsyncMock(return_value=SimpleNamespace(message_id=901)),
    )
    update = SimpleNamespace(
        message=message,
        effective_user=challenger,
        effective_chat=SimpleNamespace(id=CHAT_ID),
    )
    job_queue = SimpleNamespace(run_once=Mock())
    context = SimpleNamespace(
        args=[],
        chat_data={},
        bot_data={"spy_game": service, "master": 999, "paused": False},
        bot=SimpleNamespace(),
        job_queue=job_queue,
    )
    monkeypatch.setattr(
        duel_handlers,
        "ensure_master_in_chat_for_ai",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        duel_handlers,
        "_generate_scenario",
        AsyncMock(return_value=SCENARIO),
    )
    try:
        await duel_handlers.duel(update, context)

        assert await balance(service, 1) == 9
        challenge_text = message.reply_text.await_args.args[0]
        assert "Ставка каждого" in challenge_text
        assert "Осведомитель ×1" in challenge_text
        assert "Private" not in challenge_text
        duel_id = next(iter(context.chat_data["duels"]))
        persisted = await service.get_duel_wager(duel_id)
        assert persisted.status is DuelWagerStatus.PENDING
        assert persisted.stake_amount == 1
        assert persisted.message_id == 901
        assert job_queue.run_once.call_args.args[0] is duel_handlers.duel_accept_timeout
    finally:
        await service.close()
