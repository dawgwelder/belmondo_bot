"""Pure-Python lifecycle primitives for short group games."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from enum import Enum
from typing import Hashable


class GameError(Exception):
    """Base error for invalid game operations."""


class InvalidPhase(GameError):
    pass


class NotCreator(GameError):
    pass


class GameNotReady(GameError):
    pass


class GameFull(GameError):
    pass


class DuplicateParticipant(GameError):
    pass


class NotParticipant(GameError):
    pass


class DuplicateSubmission(GameError):
    pass


class InvalidSubmission(GameError):
    pass


class InvalidGameState(GameError):
    pass


class InvalidTimeout(GameError):
    pass


class GamePhase(str, Enum):
    LOBBY = "lobby"
    ROUND_ONE = "round_one"
    ROUND_TWO = "round_two"
    JUDGING = "judging"
    FINISHED = "finished"


class GameState:
    """Mutable state and lifecycle rules for one group game."""

    MIN_PARTICIPANTS = 2
    MAX_PARTICIPANTS = 8

    def __init__(
        self,
        game_id: str,
        game_type: str,
        chat_id: int,
        creator_id: Hashable,
        participants: Iterable[Hashable] | None = None,
        phase: GamePhase = GamePhase.LOBBY,
        submissions: Mapping[GamePhase, Mapping[Hashable, str]] | None = None,
        current_prompt_message_id: int | None = None,
        timeout_job_name: str | None = None,
        timeout_deadline: datetime | None = None,
        max_move_length: int = 1000,
    ) -> None:
        self.game_id = game_id
        self.game_type = game_type
        self.chat_id = chat_id
        self.creator_id = creator_id
        self.max_move_length = max_move_length
        self._phase = self._coerce_phase(phase)
        self._participants = (
            list(participants) if participants is not None else [creator_id]
        )
        self._current_prompt_message_id = current_prompt_message_id
        self._timeout_job_name: str | None = None
        self._timeout_deadline: datetime | None = None

        self._validate_participants()
        self._submissions = self._copy_and_validate_submissions(submissions)
        self._validate_restored_lifecycle()

        if timeout_job_name is not None or timeout_deadline is not None:
            if timeout_job_name is None or timeout_deadline is None:
                raise InvalidGameState(
                    "timeout job name and deadline must be provided together"
                )
            self.set_timeout(timeout_job_name, timeout_deadline)

    @property
    def participants(self) -> tuple[Hashable, ...]:
        return tuple(self._participants)

    @property
    def phase(self) -> GamePhase:
        return self._phase

    @property
    def submissions(self) -> dict[GamePhase, dict[Hashable, str]]:
        return {
            round_phase: dict(round_submissions)
            for round_phase, round_submissions in self._submissions.items()
        }

    @property
    def current_prompt_message_id(self) -> int | None:
        return self._current_prompt_message_id

    @property
    def timeout_job_name(self) -> str | None:
        return self._timeout_job_name

    @property
    def timeout_deadline(self) -> datetime | None:
        return self._timeout_deadline

    def join(self, user_id: Hashable) -> None:
        self._require_phase(GamePhase.LOBBY)
        if user_id in self._participants:
            raise DuplicateParticipant("participant has already joined")
        if len(self._participants) >= self.MAX_PARTICIPANTS:
            raise GameFull(
                f"game already has {self.MAX_PARTICIPANTS} participants"
            )
        self._participants.append(user_id)

    def start(self, requested_by: Hashable) -> None:
        self._require_phase(GamePhase.LOBBY)
        if requested_by != self.creator_id:
            raise NotCreator("only the creator can start the game")
        if len(self._participants) < self.MIN_PARTICIPANTS:
            raise GameNotReady(
                f"at least {self.MIN_PARTICIPANTS} participants are required"
            )
        self._phase = GamePhase.ROUND_ONE

    def submit(self, user_id: Hashable, move: str) -> None:
        if self._phase not in (GamePhase.ROUND_ONE, GamePhase.ROUND_TWO):
            raise InvalidPhase(f"cannot submit during {self._phase.value}")
        if user_id not in self._participants:
            raise NotParticipant("user is not a participant")

        round_submissions = self._submissions[self._phase]
        if user_id in round_submissions:
            raise DuplicateSubmission("participant already submitted this round")

        round_submissions[user_id] = self._validate_move(move)
        if set(round_submissions) == set(self._participants):
            self._phase = (
                GamePhase.ROUND_TWO
                if self._phase is GamePhase.ROUND_ONE
                else GamePhase.JUDGING
            )

    def advance_on_timeout(self, min_responses: int = 2) -> tuple[Hashable, ...]:
        """Close an active round using only participants who responded."""
        if self._phase not in (GamePhase.ROUND_ONE, GamePhase.ROUND_TWO):
            raise InvalidPhase(f"cannot advance timeout during {self._phase.value}")

        round_submissions = self._submissions[self._phase]
        respondents = tuple(
            user_id for user_id in self._participants if user_id in round_submissions
        )
        if len(respondents) < min_responses:
            raise GameNotReady(
                f"at least {min_responses} responses are required after timeout"
            )

        respondent_ids = set(respondents)
        self._participants = list(respondents)
        if self._phase is GamePhase.ROUND_TWO:
            self._submissions[GamePhase.ROUND_ONE] = {
                user_id: move
                for user_id, move in self._submissions[GamePhase.ROUND_ONE].items()
                if user_id in respondent_ids
            }
            self._phase = GamePhase.JUDGING
        else:
            self._phase = GamePhase.ROUND_TWO
        self.clear_timeout()
        return respondents

    def finish(self) -> None:
        self._require_phase(GamePhase.JUDGING)
        self._phase = GamePhase.FINISHED
        self.clear_timeout()

    def set_prompt_message(self, message_id: int | None) -> None:
        self._current_prompt_message_id = message_id

    def set_timeout(self, job_name: str, deadline: datetime) -> None:
        if deadline.tzinfo is None or deadline.utcoffset() is None:
            raise InvalidTimeout("timeout deadline must be timezone-aware")
        self._timeout_job_name = job_name
        self._timeout_deadline = deadline.astimezone(timezone.utc)

    def clear_timeout(self) -> None:
        self._timeout_job_name = None
        self._timeout_deadline = None

    def _validate_participants(self) -> None:
        if not self._participants:
            self._participants.append(self.creator_id)
        elif self.creator_id not in self._participants:
            raise InvalidGameState("creator must be a participant")
        if len(self._participants) > self.MAX_PARTICIPANTS:
            raise GameFull(
                f"game cannot have more than {self.MAX_PARTICIPANTS} participants"
            )
        try:
            unique_participants = set(self._participants)
        except TypeError as error:
            raise InvalidGameState("participant IDs must be hashable") from error
        if len(unique_participants) != len(self._participants):
            raise DuplicateParticipant("participants must be unique")

    def _require_phase(self, expected: GamePhase) -> None:
        if self._phase is not expected:
            raise InvalidPhase(
                f"operation requires {expected.value}, current phase is {self._phase.value}"
            )

    def _copy_and_validate_submissions(
        self,
        submissions: Mapping[GamePhase, Mapping[Hashable, str]] | None,
    ) -> dict[GamePhase, dict[Hashable, str]]:
        copied = {
            GamePhase.ROUND_ONE: {},
            GamePhase.ROUND_TWO: {},
        }
        if submissions is None:
            return copied

        unexpected_phases = set(submissions) - set(copied)
        if unexpected_phases:
            raise InvalidGameState("submissions contain an invalid round")

        participant_ids = set(self._participants)
        for round_phase, supplied_round in submissions.items():
            if not isinstance(supplied_round, Mapping):
                raise InvalidGameState("round submissions must be a mapping")
            if not set(supplied_round).issubset(participant_ids):
                raise InvalidGameState("submissions contain a non-participant")
            try:
                copied[self._coerce_phase(round_phase)] = {
                    user_id: self._validate_move(move)
                    for user_id, move in supplied_round.items()
                }
            except InvalidSubmission as error:
                raise InvalidGameState(
                    "restored submissions contain an invalid move"
                ) from error
        return copied

    def _validate_restored_lifecycle(self) -> None:
        participant_ids = set(self._participants)
        first_ids = set(self._submissions[GamePhase.ROUND_ONE])
        second_ids = set(self._submissions[GamePhase.ROUND_TWO])
        first_complete = first_ids == participant_ids
        second_complete = second_ids == participant_ids

        valid = {
            GamePhase.LOBBY: not first_ids and not second_ids,
            GamePhase.ROUND_ONE: not first_complete and not second_ids,
            GamePhase.ROUND_TWO: first_complete and not second_complete,
            GamePhase.JUDGING: first_complete and second_complete,
            GamePhase.FINISHED: first_complete and second_complete,
        }
        if not valid[self._phase]:
            raise InvalidGameState(
                f"submissions are inconsistent with {self._phase.value}"
            )

    def _validate_move(self, move: str) -> str:
        if not isinstance(move, str):
            raise InvalidSubmission("move must be text")
        move = move.strip()
        if not move or len(move) > self.max_move_length:
            raise InvalidSubmission(
                f"move must contain 1-{self.max_move_length} characters"
            )
        return move

    @staticmethod
    def _coerce_phase(phase: GamePhase) -> GamePhase:
        try:
            return GamePhase(phase)
        except ValueError as error:
            raise InvalidGameState(f"unknown game phase: {phase}") from error
