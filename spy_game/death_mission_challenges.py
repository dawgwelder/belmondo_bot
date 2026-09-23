"""Small persistent archive puzzle. The server validates each adjacent move."""

import hashlib
from collections import deque
from copy import deepcopy


def board(seed: str, node: int) -> dict:
    digest = hashlib.sha256(f"archive-v1:{seed}:{node}".encode()).digest()
    # Random monotone path guarantees a solution within six moves. Remaining
    # cells can be traps; the public board contains everything needed to solve.
    steps = sorted(enumerate((1, 1, 1, 4, 4, 4)), key=lambda pair: digest[pair[0]])
    position = 0
    safe = {0}
    for _, delta in steps:
        position += delta
        safe.add(position)
    return dict(
        id=f"archive-{node}",
        version=1,
        size=4,
        start=0,
        goal=15,
        blocked=[i for i in range(16) if i not in safe and digest[8 + i] % 3 != 0],
        path=[0],
        moves_left=8,
    )


def neighbours(challenge: dict, cell: int) -> list[int]:
    size = challenge["size"]
    return [
        other
        for other in (cell - size, cell + size, cell - 1, cell + 1)
        if 0 <= other < size * size
        and abs(other // size - cell // size) + abs(other % size - cell % size) == 1
        and other not in challenge["blocked"]
    ]


def moves(challenge: dict) -> list[int]:
    return neighbours(challenge, challenge["path"][-1])


def advance(challenge: dict, cell: int) -> tuple[dict, str | None]:
    if challenge["moves_left"] <= 0 or cell not in moves(challenge):
        raise ValueError("Недоступная клетка")
    result = deepcopy(challenge)
    result["path"].append(cell)
    result["moves_left"] -= 1
    outcome = "solved" if cell == result["goal"] else "missed" if not result["moves_left"] else None
    return result, outcome


def next_step(challenge: dict) -> str:
    """Public-board solver for offline perfect-play economic validation."""
    start = challenge["path"][-1]
    queue = deque([(start, [])])
    visited = {start}
    while queue:
        cell, path = queue.popleft()
        if cell == challenge["goal"] and path:
            return f"cell_{path[0]}" if len(path) <= challenge["moves_left"] else "skip_puzzle"
        for other in neighbours(challenge, cell):
            if other not in visited:
                visited.add(other)
                queue.append((other, path + [other]))
    return "skip_puzzle"
