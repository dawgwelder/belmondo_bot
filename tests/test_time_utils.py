from datetime import datetime, timedelta, tzinfo

import pytest

from spy_game.duels import _datetime as duel_datetime
from spy_game.persistence.base import _datetime as repository_datetime
from time_utils import MOSCOW, moscow_iso, moscow_now


@pytest.mark.parametrize(
    "source",
    [
        "2026-10-15T21:30:00.123456+00:00",
        "2026-10-16T00:30:00.123456+03:00",
        "2026-10-16T03:30:00.123456+06:00",
    ],
)
def test_serialization_preserves_instant_and_microseconds_across_midnight(source):
    original = datetime.fromisoformat(source)
    encoded = moscow_iso(original)
    assert encoded == "2026-10-16T00:30:00.123456+03:00"
    assert datetime.fromisoformat(encoded).timestamp() == original.timestamp()
    for decode in (repository_datetime, duel_datetime):
        decoded = decode(source)
        assert decoded == original
        assert decoded.tzinfo is MOSCOW
        assert decode(None) is None


class UnspecifiedOffset(tzinfo):
    def utcoffset(self, value):
        return None


@pytest.mark.parametrize("zone", [None, UnspecifiedOffset()])
def test_serialization_rejects_datetimes_without_an_offset(zone):
    with pytest.raises(ValueError, match="timezone-aware"):
        moscow_iso(datetime(2026, 10, 15, tzinfo=zone))


def test_clock_uses_moscow_without_changing_elapsed_time():
    before = datetime.now(MOSCOW)
    current = moscow_now()
    after = datetime.now(MOSCOW)
    assert before <= current <= after
    assert current.tzinfo is MOSCOW
    assert current.utcoffset() == timedelta(hours=3)
