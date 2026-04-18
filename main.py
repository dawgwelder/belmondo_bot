"""Backwards-compatible entrypoint.

Real implementation lives in :mod:`app`. This module exists so existing
launch commands such as ``python3 main.py --token=...`` keep working.
"""

import fire

from app import run_bot

if __name__ == "__main__":
    fire.Fire(run_bot)
