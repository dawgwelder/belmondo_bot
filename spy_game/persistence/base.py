"""Shared repository dependencies and UTC serialization."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from datetime import datetime, timezone
import uuid

from ..rewards import RewardResolver
from ..scheduler import ActivityPolicy, RandomSource
from ..settings import SpySettings


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _datetime(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _new_event_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class RepositoryContext:
    settings: SpySettings
    activity_policy: ActivityPolicy
    rng: RandomSource
    reward_resolver: RewardResolver
    event_id_factory: Callable[[], str] = _new_event_id


class RepositoryComponent:
    """Dependency access only; game operations live in concrete repositories."""

    def __init__(self, context: RepositoryContext) -> None:
        self.context = context

    @property
    def settings(self) -> SpySettings:
        return self.context.settings

    @settings.setter
    def settings(self, value: SpySettings) -> None:
        self.context.settings = value

    @property
    def activity_policy(self) -> ActivityPolicy:
        return self.context.activity_policy

    @property
    def rng(self) -> RandomSource:
        return self.context.rng

    @property
    def reward_resolver(self) -> RewardResolver:
        return self.context.reward_resolver

    @property
    def event_id_factory(self) -> Callable[[], str]:
        return self.context.event_id_factory
