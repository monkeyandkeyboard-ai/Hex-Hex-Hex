"""Hex A* pathfinding within a bounded floor disc. The heuristic is exact
(hex distance is the true minimum move cost on an unweighted grid), so this
is optimal and won't re-expand nodes.
"""
import heapq
from typing import Callable

Tile = tuple[int, int]


def hex_neighbors(q: int, r: int) -> list[Tile]:
    return [
        (q + 1, r),
        (q - 1, r),
        (q, r + 1),
        (q, r - 1),
        (q + 1, r - 1),
        (q - 1, r + 1),
    ]


def hex_dist(a: Tile, b: Tile) -> int:
    aq, ar = a
    bq, br = b
    return max(abs(aq - bq), abs(ar - br), abs((-aq - ar) - (-bq - br)))


def find_path(
    start: Tile,
    goal: Tile,
    is_passable: Callable[[Tile], bool],
) -> list[Tile] | None:
    """Returns the path from start to goal (inclusive of both endpoints) or
    None if no path exists. is_passable should return True for walkable tiles
    (including the goal even if it has an entity on it -- the caller decides
    whether the destination is legal).
    """
    if start == goal:
        return [start]

    open_heap: list[tuple[int, int, Tile]] = []
    seq = 0
    heapq.heappush(open_heap, (hex_dist(start, goal), seq, start))

    came_from: dict[Tile, Tile | None] = {start: None}
    g: dict[Tile, int] = {start: 0}

    while open_heap:
        _, _, current = heapq.heappop(open_heap)

        if current == goal:
            path = []
            node: Tile | None = current
            while node is not None:
                path.append(node)
                node = came_from.get(node)
            path.reverse()
            return path

        cg = g[current]
        for neighbor in hex_neighbors(*current):
            if neighbor not in came_from and (neighbor == goal or is_passable(neighbor)):
                ng = cg + 1
                g[neighbor] = ng
                came_from[neighbor] = current
                seq += 1
                heapq.heappush(open_heap, (ng + hex_dist(neighbor, goal), seq, neighbor))

    return None
