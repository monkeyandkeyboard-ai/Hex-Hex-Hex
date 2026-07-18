"""Road spine for a floor: a path connecting the floor's entrance to its exit.

Uses the shared hex A*. Roads are a layout overlay -- they don't block
movement, but monster placement biases away from road tiles so the road
reads as the relatively safe route (off-road is more dangerous).

Endpoints:
  - Floors with both exits: down_exit (entrance from below) -> up_exit.
  - Floor 1 (no down_exit): spawn center (0, 0) -> up_exit.
"""
from gep.pathfinding import find_path

Tile = tuple[int, int]


def build_roads(
    tile_set: set[Tile],
    up_exit: Tile | None,
    down_exit: Tile | None,
) -> set[Tile]:
    if up_exit is None:
        return set()

    start = down_exit if down_exit is not None else (0, 0)
    if start not in tile_set:
        start = (0, 0)

    path = find_path(start, up_exit, lambda t: t in tile_set)
    return set(path) if path else {start, up_exit}
