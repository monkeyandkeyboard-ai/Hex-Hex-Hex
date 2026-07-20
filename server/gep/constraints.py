"""Hard constraints on the physical map, enforced after macro layout runs.

Map generation must produce a layout that is *guaranteed* correct, not merely
probable -- a loose probability (e.g. "warp is usually small enough that
exits stay reachable") is exactly the kind of thing that looks fine for a
thousand floors and then produces an unreachable exit on floor 1001. These
checks make that class of failure a startup-time GenerationError instead of
a live-floor bug report.

Two constraints:
  - connectivity: every exit tile must be reachable from the floor's spawn
    point over passable tiles. Every tile is passable today (terrain is
    cosmetic), so this always holds now -- it exists so a future impassable
    terrain feature is required to prove it hasn't walled off an exit.
  - biome adjacency: archetype-declared forbidden biome pairs may not sit on
    neighbouring tiles. Violations are repaired in place (the offending tile
    falls back to the template's fallback biome) rather than failing the
    floor outright, since a single stray boundary tile is a cosmetic
    correction, not a structural failure. If repair can't clear every
    violation the floor is rejected -- that means the template's own rules
    conflict with each other, which is a config bug, not bad luck on a roll.
"""
from gep.pathfinding import find_path, hex_neighbors

Tile = tuple[int, int]


class GenerationError(Exception):
    pass


def validate_connectivity(
    tile_set: set[Tile],
    spawn_point: Tile,
    exits: list[Tile],
) -> None:
    for exit_tile in exits:
        if exit_tile not in tile_set:
            raise GenerationError(f"exit {exit_tile} is not on the floor")
        if find_path(spawn_point, exit_tile, lambda t: t in tile_set) is None:
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
