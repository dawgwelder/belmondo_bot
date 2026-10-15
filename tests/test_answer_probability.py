import pytest

import utils


@pytest.mark.parametrize(
    ("mode", "value", "expected"),
    [
        ("chaos", 0, False),
        ("chaos", 0.001, True),
        ("soft", 0.5, False),
        ("soft", 0.501, True),
        ("medium", 0.749, False),
        ("medium", 0.75, True),
        ("rare", 0.949, False),
        ("rare", 0.95, True),
    ],
)
def test_probability_thresholds_are_unchanged(monkeypatch, mode, value, expected):
    monkeypatch.setattr(utils.random, "random", lambda: value)
    assert utils.answer_probability(mode) is expected


def test_unknown_mode_keeps_default():
    assert utils.answer_probability("unknown") == 0.75
