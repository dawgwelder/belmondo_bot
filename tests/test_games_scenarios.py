import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from games.scenarios import SCENARIOS


def test_all_planned_scenarios_are_registered():
    assert set(SCENARIOS) == {"alibi", "operation", "pitch"}
    assert all(scenario.command == "/game" for scenario in SCENARIOS.values())
