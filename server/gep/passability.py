"""Which tiles terrain forbids standing on.

Until now every tile on a floor was walkable and terrain was purely cosmetic
-- gep/constraints.py said so in as many words, and noted that the
connectivity check existed so that a future impassable feature would be
*required* to prove it hadn't walled off an exit. This module is that future
feature's foundation.

Passability is a property of the biome, not a parallel overlay. A tile already
has exactly one biome and the biome file already owns everything else about
what that tile is (colour, texture, what spawns on it), so putting `passable`
anywhere else would create a second source of truth for the same question.
Water and mountains are then ordinary biomes that happen to say `false`, and
they inherit the whole existing config pipeline for free.

Two distinct notions of "blocked" meet at movement time and are deliberately
kept apart:
  - terrain, computed here once at generation and fixed for the life of the
    floor. A cliff does not move.
  - entities, computed per call in FloorState.is_passable, because a monster
    standing in a doorway blocks it only until it dies.
Generation can only reason about the first -- there are no entities yet when
roads are carved -- which is exactly why connectivity is validated against
terrain alone. A floor whose exits are reachable except when a monster happens
to be standing somewhere is still a correctly generated floor.
"""
from typing import Callable

from gep.hexgrid import hex_distance
from gep.pathfinding import find_path, hex_neighbors

Tile = tuple[int, int]


def blocked_tiles(regions: dict[Tile, str], biome_defs: dict | None) -> set[Tile]:
    """Every tile whose biome forbids entry.

    A tile with no region (the legacy generation path assigns none) is
    walkable: absence of terrain data means terrain imposes no restriction,
    not that the floor is solid rock. An unknown biome id is likewise treated
    as passable rather than raising -- ConfigStore already rejects archetypes
    that name a biome it can't resolve, so reaching here with one means the
    caller passed a partial biome table, which tests legitimately do.
    """
    if not biome_defs:
        return set()
    impassable = {
        bid for bid, data in biome_defs.items()
        if data.get("passable") is False
    }
    if not impassable:
        return set()
    return {tile for tile, bid in regions.items() if bid in impassable}


def clear_structural_tiles(
    regions: dict[Tile, str],
    reserved: list[Tile],
    fallback_biome: str | None,
    biome_defs: dict | None,
) -> dict[Tile, str]:
    """Force the floor's fixed structural tiles back to walkable terrain.

    Exits are chosen on the outer ring *before* any terrain exists, so nothing
    stops a later stage from dropping a lake on a staircase. That is not a rare
    unlucky roll to be caught by validation and rejected -- with enough water on
    enough floors it is a certainty, and rejecting the floor is no fix because
    the exit position is not re-rollable at that point.

    So the structural tiles win outright. The tile's *region* is rewritten
    rather than just exempting it from the blocked set, because the two must
    agree: exempting it alone would render a staircase submerged in a lake that
    the player then walks across.

    Only the tiles themselves are cleared, not a margin around them. A staircase
    on a shoreline is fine and reads well; whether the player can reach it from
    the other exit is the connectivity check's question, not this one's.

    Strictly need-driven: a structural tile on terrain that was already walkable
    is left exactly as it is. Rewriting unconditionally would mean the tower
    entrance stopped being town biome on a town floor merely because a rule
    about lakes exists, which is a rule reaching somewhere it has no business.
    """
    if not fallback_biome or not biome_defs:
        return regions
    impassable = {
        bid for bid, data in biome_defs.items() if data.get("passable") is False
    }
    if not impassable:
        return regions
    for tile in reserved:
        if regions.get(tile) in impassable:
            regions[tile] = fallback_biome
    return regions


def carve_crossings(
    regions: dict[Tile, str],
    blocked: set[Tile],
    tile_set: set[Tile],
    spawn_point: Tile,
    exits: list[Tile],
    fallback_biome: str | None,
) -> set[Tile]:
    """Guarantee every exit is reachable, carving through barriers if needed.

    Returns the tiles that were carved, which is the set the feature pass will
    render as bridges and fords -- a crossing is not decoration applied over a
    river, it is the tile where the river was opened.

    Why this exists rather than a validate-and-reject loop: a barrier biome
    covering even a modest share of a hex disc cuts it in two most of the time.
    Blobby low-elevation terrain does not politely leave a corridor, and the
    exits sit on the outer ring where a barrier reaching the edge isolates
    them completely. Rejecting those floors would mean rejecting most floors,
    and re-rolling is not available -- the seed *is* the floor.

    So connectivity is produced rather than checked. For each unreachable exit
    we path to it ignoring barriers, then open only the blocked tiles on that
    path. That is a minimal cut by construction: A* returns a shortest route,
    so no tile is opened that a shorter crossing would have avoided, and the
    barrier stays a barrier everywhere the player does not actually need to
    pass. `validate_connectivity` still runs afterwards and is still able to
    fail -- this widens the set of templates that generate, it does not remove
    the guarantee that a shipped floor is playable.

    Deterministic: A* is deterministic given the same grid, and exits are
    processed in the order given, so the carved set is a pure function of the
    layout. No PRNG is consumed, which also means adding this cannot shift any
    existing seed's terrain.
    """
    if not blocked or not fallback_biome:
        return set()

    carved: set[Tile] = set()
    for exit_tile in exits:
        # Re-derived each pass: carving for one exit may already have opened
        # the route to the next, and re-using a stale predicate would carve a
        # second redundant crossing through the same barrier.
        passable = terrain_predicate(tile_set, blocked)
        if find_path(spawn_point, exit_tile, passable) is not None:
            continue

        # Ignore barriers entirely to find where a crossing has to go.
        route = find_path(spawn_point, exit_tile, lambda t: t in tile_set)
        if route is None:
            continue  # genuinely off-floor; validate_connectivity reports it

        for tile in route:
            if tile in blocked:
                blocked.discard(tile)
                regions[tile] = fallback_biome
                carved.add(tile)
    return carved


def open_components(
    tile_set: set[Tile],
    blocked: set[Tile],
) -> list[list[Tile]]:
    """Connected groups of walkable tiles, largest first.

    Flood filled in canonical tile order and returned sorted by (size
    descending, first tile) so the ordering is total and seed-independent --
    two components of equal size must not swap places between runs.
    """
    open_tiles = [t for t in sorted(tile_set) if t not in blocked]
    remaining = set(open_tiles)
    out: list[list[Tile]] = []
    for tile in open_tiles:
        if tile not in remaining:
            continue
        group, stack = [], [tile]
        remaining.discard(tile)
        while stack:
            current = stack.pop()
            group.append(current)
            for n in hex_neighbors(*current):
                if n in remaining:
                    remaining.discard(n)
                    stack.append(n)
        out.append(sorted(group))
    out.sort(key=lambda g: (-len(g), g[0]))
    return out


def reconnect_islands(
    regions: dict[Tile, str],
    blocked: set[Tile],
    tile_set: set[Tile],
    spawn_point: Tile,
    fallback_biome: str | None,
    min_island_tiles: int,
) -> set[Tile]:
    """Open a crossing to every sizeable pocket of walkable ground cut off from
    the main body of the floor.

    Reaching the exits is not the same as the floor being playable. Measured on
    the flooded ruins archetype before this existed: rivers drawn across a
    chamber floor left 40-80% of the carved rooms unreachable -- rooms the
    player can see across the water and never enter. Every exit was reachable
    the whole time, so the exit-level guarantee reported success.

    Deliberately *not* "reconnect everything". A three-tile ledge behind a
    waterfall is scenery and walling it off is fine; the map does not have to
    be exhaustively traversable, and forcing that would dissolve the barriers
    into decoration. `min_island_tiles` is where scenery ends and a stranded
    wing of the dungeon begins, and it is config, not a constant here, because
    that line is a pacing judgement.

    Islands are processed largest first, and the reachable set is recomputed
    after each carve, so opening a route to a big pocket that happens to also
    touch a small one does not then carve a second redundant crossing.
    """
    if not blocked or not fallback_biome or min_island_tiles <= 0:
        return set()

    carved: set[Tile] = set()
    while True:
        components = open_components(tile_set, blocked)
        main = next((c for c in components if spawn_point in set(c)), None)
        if main is None:
            return carved
        main_set = set(main)

        island = next(
            (c for c in components
             if c[0] not in main_set and len(c) >= min_island_tiles),
            None,
        )
        if island is None:
            return carved

        # Bridge from the island tile closest to the main body, so the crossing
        # is the short span it would be in reality rather than a tunnel from
        # wherever the flood fill happened to start.
        source = min(island, key=lambda t: (min(hex_distance(t, m) for m in main), t))
        target = min(main, key=lambda m: (hex_distance(source, m), m))
        route = find_path(source, target, lambda t: t in tile_set)
        if route is None:
            return carved

        opened = False
        for tile in route:
            if tile in blocked:
                blocked.discard(tile)
                regions[tile] = fallback_biome
                carved.add(tile)
                opened = True
        if not opened:
            # Nothing left to open on the shortest span; further passes would
            # loop on the same island forever.
            return carved


def terrain_predicate(
    tile_set: set[Tile] | frozenset[Tile],
    blocked: set[Tile] | frozenset[Tile],
) -> Callable[[Tile], bool]:
    """The passability test generation uses: on the floor, and not walled off.

    Handed to A* rather than inlined at each call site so that roads and the
    connectivity check are provably asking the same question. Carving a road
    under one rule and validating reachability under another is the specific
    bug this shape prevents -- it would pass validation and strand the player.
    """
    return lambda t: t in tile_set and t not in blocked
