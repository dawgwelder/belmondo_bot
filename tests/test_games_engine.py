from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from games import (
    DuplicateParticipant,
    DuplicateSubmission,
    GameAlreadyActive,
    GameFull,
    GameNotReady,
    GamePhase,
    GameState,
    GameStore,
    InvalidGameState,
    InvalidPhase,
    InvalidSubmission,
    InvalidTimeout,
    NotCreator,
    NotParticipant,
)


def make_game(**overrides):
    values = {
        "game_id": "game-1",
        "game_type": "generic",
        "chat_id": -100,
        "creator_id": 1,
    }
    values.update(overrides)
    return GameState(**values)


def ready_game():
    game = make_game()
    game.join(2)
    game.join(3)
    game.start(1)
    return game


def play_to_judging(game):
    for user_id in game.participants:
        game.submit(user_id, f"round one by {user_id}")
    for user_id in game.participants:
        game.submit(user_id, f"round two by {user_id}")
    return game


def test_new_game_is_a_lobby_with_creator_as_first_participant():
    game = make_game()

    assert game.phase is GamePhase.LOBBY
    assert game.participants == (1,)
    assert game.submissions == {
        GamePhase.ROUND_ONE: {},
        GamePhase.ROUND_TWO: {},
    }


def test_lobby_accepts_up_to_eight_unique_participants():
    game = make_game()

    with pytest.raises(DuplicateParticipant):
        game.join(1)

    for user_id in range(2, 9):
        game.join(user_id)

    assert game.participants == tuple(range(1, 9))
    with pytest.raises(GameFull):
        game.join(9)


def test_only_creator_can_start_and_at_least_two_players_are_required():
    game = make_game()

    with pytest.raises(NotCreator):
        game.start(2)
    with pytest.raises(GameNotReady):
        game.start(1)

    game.join(2)
    game.start(1)

    assert game.phase is GamePhase.ROUND_ONE


def test_submit_rejects_duplicates_outsiders_invalid_moves_and_invalid_phases():
    game = ready_game()

    game.submit(1, "first")
    with pytest.raises(DuplicateSubmission):
        game.submit(1, "again")
    with pytest.raises(NotParticipant):
        game.submit(99, "intrusion")
    for move in ("", "   ", "x" * 1001):
        with pytest.raises(InvalidSubmission):
            game.submit(2, move)

    completed = ready_game()
    play_to_judging(completed)
    with pytest.raises(InvalidPhase):
        completed.submit(1, "too late")


def test_completing_rounds_advances_to_judging_and_finish_clears_timeout():
    game = ready_game()

    for user_id in game.participants:
        game.submit(user_id, f"round one by {user_id}")
    assert game.phase is GamePhase.ROUND_TWO

    for user_id in game.participants:
        game.submit(user_id, f"round two by {user_id}")
    assert game.phase is GamePhase.JUDGING

    game.set_timeout("job", datetime(2026, 7, 11, tzinfo=timezone.utc))
    game.finish()
    assert game.phase is GamePhase.FINISHED
    assert game.timeout_job_name is None


def test_timeout_prunes_non_respondents_and_requires_two_answers():
    game = ready_game()
    game.submit(1, "one")
    with pytest.raises(GameNotReady):
        game.advance_on_timeout()

    game.submit(2, "two")
    assert game.advance_on_timeout() == (1, 2)
    assert game.phase is GamePhase.ROUND_TWO
    assert game.participants == (1, 2)

    game.submit(1, "second")
    game.submit(2, "second")
    assert game.phase is GamePhase.JUDGING


def test_constructor_rejects_malformed_restored_state():
    with pytest.raises(InvalidGameState):
        make_game(
            participants=[1, 2, 3],
            phase=GamePhase.ROUND_TWO,
            submissions={
                GamePhase.ROUND_ONE: {1: "one"},
                GamePhase.ROUND_TWO: {},
            },
        )

    with pytest.raises(GameFull):
        make_game(participants=list(range(1, 10)))


def test_timeout_metadata_must_be_timezone_aware_and_is_normalized():
    game = make_game()

    with pytest.raises(InvalidTimeout):
        game.set_timeout("job", datetime(2026, 7, 11))

    game.set_timeout(
        "job",
        datetime(2026, 7, 11, 15, tzinfo=timezone(timedelta(hours=3))),
    )
    assert game.timeout_deadline == datetime(2026, 7, 11, 12, tzinfo=timezone.utc)


def test_store_enforces_one_active_game_per_chat():
    store = GameStore()
    game = make_game()
    store.add(game)

    assert store.get(game.chat_id) is game
    with pytest.raises(GameAlreadyActive):
        store.add(make_game(game_id="game-2"))

    game.join(2)
    game.join(3)
    game.start(1)
    play_to_judging(game)
    game.finish()
    replacement = make_game(game_id="game-2")

    store.add(replacement)
    assert store.remove(replacement.chat_id) is replacement
    assert store.remove(replacement.chat_id) is None
