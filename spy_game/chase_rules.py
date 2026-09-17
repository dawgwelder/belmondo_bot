"""Bounded chase timing and additive, varied prize bundles."""
from .models import DropReward

CHASE_SECONDS = (30, 25, 20, 15, 10, 5)


def chase_bonus(turn, rng):
    pools = (
        (DropReward("agent", "informant", 1),),
        tuple(
            DropReward("agent", key, 1) for key in ("operative", "observer", "courier")
        ),
        tuple(
            DropReward("item", key, 1)
            for key in ("intel_file", "fake_passport", "radio")
        ),
        tuple(
            DropReward("agent", key, 1)
            for key in ("analyst", "saboteur", "sleeper", "double_agent")
        ),
        tuple(
            DropReward("item", key, 1)
            for key in ("fake_passport", "satellite_image", "access_code")
        ),
        tuple(
            DropReward("agent", key, 1)
            for key in ("operative", "observer", "courier", "analyst")
        ),
    )
    pool = pools[turn - 1]
    return pool[rng.randint(0, len(pool) - 1)]
