"""Hard constraints on the physical map, enforced after macro layout runs.

Map generation must produce a layout that is *guaranteed* correct, not merely
probable -- a loose probability (e.g. "warp is usually small enough that
exits stay reachable") is exactly the kind of thing that looks fine for a
thousand floors and then produces an unreachable exit on floor 1001. These
checks make that class of failure a startup-time GenerationError instead of
a live-floor bug report.

Two constraints:
  - connectivity: every exit tile must be reachable from the floor's spawn
    point over passable tiles. This was a tautology while all terrain was
    cosmetic; now that a biome can declare itself impassable (see
    gep/passability.py) it is load-bearing, and a template whose terrain walls
    off an exit fails generation instead of shipping an unplayable floor.
  - biome adjacency: archetype-declared forbidden biome pairs may not sit on
    neighbouring tiles. Violations are repaired in place (the offending tile
    falls back to the template's fallback biome) rather than failing the
    floor outright, since a single stray boundary tile is a cosmetic
    correction, not a structural failure. If repair can't clear every
    violation the floor is rejected -- that means the template's own rules
    conflict with each other, which is a config bug, not bad luck on a roll.
"""
from typing import Callable

from gep.pathfinding import find_path, hex_neighbors

Tile = tuple[int, int]


class GenerationError(Exception):
    pass


def validate_connectivity(
    tile_set: set[Tile],
    spawn_point: Tile,
    exits: list[Tile],
    is_passable: Callable[[Tile], bool] | None = None,
) -> None:
    """Every exit must be reachable from spawn over passable terrain.

    An exit or the spawn point sitting *on* blocked terrain is reported
    separately from an exit merely being cut off, because they are different
    config bugs: the first means a feature was stamped over a fixed structural
    tile, the second means a barrier closed the only route between them.
    """
    if is_passable is None:
        def is_passable(t: Tile) -> bool:
            return t in tile_set

    if not is_passable(spawn_point):
        raise GenerationError(
            f"spawn {spawn_point} is on impassable terrain -- a feature was "
            f"placed over the floor's entrance"
        )
    for exit_tile in exits:
        if exit_tile not in tile_set:
            raise GenerationError(f"exit {exit_tile} is not on the floor")
        if not is_passable(exit_tile):
            raise GenerationError(
                f"exit {exit_tile} is on impassable terrain -- a feature was "
                f"placed over it"
            )
        if find_path(spawn_point, exit_tile, is_passable) is None:
            raise GenerationError(
                f"exit {exit_tile} is not reachable from spawn {spawn_point}"
            )


def _adjacent_violations(
    regions: dict[Tile, str],
    forbidden_pairs: set[frozenset[str]],
) -> list[Tile]:
    violating: list[Tile] = []
    for tile, biome in regions.items():
        for neighbor in hex_neighbors(*tile):
            other = regions.get(neighbor)
            if other is not None and frozenset((biome, other)) in forbidden_pairs:
                violating.append(tile)
                break
    return violating


def enforce_biome_adjacency(
    regions: dict[Tile, str],
    forbidden_adjacent_biomes: list[list[str]],
    fallback_biome: str | None,
    max_passes: int = 3,
) -> dict[Tile, str]:
    """Repair forbidden-adjacency violations in place, in canonical tile
    order so the outcome is deterministic regardless of dict iteration.
    Raises GenerationError if violations remain after `max_passes` -- at that
    point the template's own constraints are unsatisfiable and no amount of
    repair will converge.
    """
    if not forbidden_adjacent_biomes:
        return regions
    forbidden_pairs = {frozenset(pair) for pair in forbidden_adjacent_biomes}

    for _ in range(max_passes):
        violating = _adjacent_violations(regions, forbidden_pairs)
        if not violating:
            return regions
        if fallback_biome is None:
            raise GenerationError(
                "biome adjacency violated but template has no fallback_biome to repair with"
            )
        for tile in violating:
            regions[tile] = fallback_biome

    violating = _adjacent_violations(regions, forbidden_pairs)
    if violating:
        raise GenerationError(
            f"biome adjacency constraints unsatisfiable after {max_passes} repair passes: "
            f"{len(violating)} tile(s) still violating"
        )
    return regions
