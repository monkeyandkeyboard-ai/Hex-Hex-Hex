"""Road spine for a floor: a path connecting the floor's entrance to its exit.

Uses the shared hex A*. Roads are a layout overlay -- they don't block
movement, but monster placement biases away from road tiles so the road
reads as the relatively safe route (off-road is more dangerous).

Endpoints:
  - Floors with both exits: down_exit (entrance from below) -> up_exit.
  - Floor 1 (no down_exit): spawn center (0, 0) -> up_exit.

The road routes *around* impassable terrain rather than through it, because
it takes the same passability predicate the connectivity check will run a
moment later. If it took a laxer one it would happily draw a road across a
lake, and the floor would validate -- the road overlay is not what
connectivity inspects.
"""
from typing import Callable

from gep.pathfinding import find_path

Tile = tuple[int, int]


def build_roads(
    tile_set: set[Tile],
    up_exit: Tile | None,
    down_exit: Tile | None,
    is_passable: Callable[[Tile], bool] | None = None,
) -> set[Tile]:
    """`is_passable` defaults to bare floor membership, matching the behaviour
    from before terrain could block. Callers inside the pipeline always pass
    the real predicate; the default is for the legacy path and for tests that
    build a floor with no biome data to have an opinion about."""
    if up_exit is None:
        return set()

    if is_passable is None:
        def is_passable(t: Tile) -> bool:
            return t in tile_set

    start = down_exit if down_exit is not None else (0, 0)
    if start not in tile_set:
        start = (0, 0)

    path = find_path(start, up_exit, is_passable)
    return set(path) if path else {start, up_exit}
