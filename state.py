"""Mutable, process-wide state container.

Kept separate from `const.py` so constants and prompts stay immutable, and
mutable runtime state has an explicit home.
"""

from const import SELF_ID, SELF_ID_DEV

vars_dict = {
    "ZAVOD_CHECK": False,
    "dt": None,
    "zavod_text": "",
    "spam_mode": "medium",
    "username": None,
    "paused": False,
    "spam_stopper": {},
    "self_id_dev": SELF_ID_DEV,
    "self_id": SELF_ID,
    "master": 113300226,
}
