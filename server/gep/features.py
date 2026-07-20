"""Topological features stamped over a finished macro layout: rivers and
chambers.

These are deliberately *not* layout modes. A layout mode answers "what biome is
this tile" for every tile independently of every other; a feature answers "where
does this structure run", which is a path or a shape, and the tiles it covers
follow from that. Forcing rivers into gep/biome_layout.py would mean a fourth
mode that ignores the mode contract, and every future feature would add another
-- so features get their own stage and their own module, and the layout modes
stay three.

Both stages run *after* the macro layout and overwrite what it wrote. That
ordering is the whole point: they inherit a coherent biome field to cut into,
and they land before the passability/crossing pass, so a river that bisects the
floor is resolved by the same crossing carver that handles every other barrier
(see gep/passability.py). Rivers therefore never need to reason about their own
fords -- the guarantee is structural, not something this module reimplements.

Determinism: each feature draws from its own named PRNG stream
(`{floor_seed}:rivers`, `{floor_seed}:chambers`) rather than a shared one, so
adding, removing or retuning one feature cannot shift the other's output, and
neither can shift the macro layout's. Same reasoning as _layout_rng in
gep/biome_layout.py.
"""
from gep.hexgrid import hex_distance
from gep.pathfinding import find_path, hex_neighbors
from gep.prng import Mulberry32, fnv1a32
from gep.rolls import weighted_pick

Tile = tuple[int, int]
CENTER: Tile = (0, 0)


def _stream(floor_seed: int, name: str) -> Mulberry32:
    return Mulberry32(fnv1a32(f"{floor_seed}:{name}".encode("utf-8")))


def _pick(rng: Mulberry32, items: list):
    """Uniform choice over a list, consuming exactly one draw."""
    return items[min(len(items) - 1, int(rng.next_float() * len(items)))]


# --- Rivers ---------------------------------------------------------------

def _ring(tiles: list[Tile], radius: int) -> list[Tile]:
    return [t for t in tiles if hex_distance(CENTER, t) == radius]


def _opposite(tile: Tile) -> Tile:
    """The tile diametrically across the disc. A river between a point and its
    opposite crosses the centre region, which is what makes it a barrier worth
    carving rather than a puddle clipping one edge."""
    return (-tile[0], -tile[1])


def river_path(
    rng: Mulberry32,
    tile_set: set[Tile],
    source: Tile,
    mouth: Tile,
    meander: float,
    max_steps: int,
) -> list[Tile]:
    """Walk from source to mouth, preferring progress but allowed to wander.

    Not A*: a shortest path is a straight line, and a straight line does not
    read as a river. Each step scores the neighbours that close the distance
    against those that hold it level, and `meander` sets how often the walk
    takes the lateral option. Progress-reducing steps are always available
    while the mouth is reachable, so the walk terminates.

    Backtracking is excluded rather than penalised -- a river that revisits a
    tile has doubled back on itself, which reads as a mistake regardless of how
    unlikely it is.
    """
    path: list[Tile] = [source]
    seen = {source}
    current = source

    for _ in range(max_steps):
        if current == mouth:
            break
        d = hex_distance(current, mouth)
        closer, level = [], []
        for n in hex_neighbors(*current):
            if n not in tile_set or n in seen:
                continue
            nd = hex_distance(n, mouth)
            if nd < d:
                closer.append(n)
            elif nd == d:
                level.append(n)

        if not closer and not level:
            break  # walked into a dead end against the disc edge
        # One draw decides the branch and one decides the tile, always in that
        # order, so the stream advances identically whichever branch is taken.
        take_level = bool(level) and rng.next_float() < meander
        current = _pick(rng, level if take_level else (closer or level))
        path.append(current)
        seen.add(current)

    return path


def widen(
    rng: Mulberry32,
    path: list[Tile],
    tile_set: set[Tile],
    width_chance: float,
) -> set[Tile]:
    """Thicken a 1-tile path to 1-2 tiles.

    Widening is per-tile rather than per-river so a river narrows and broadens
    along its length instead of being uniformly one width, and it draws in
    canonical neighbour order so the choice is a function of the stream alone.
    """
    tiles = set(path)
    for tile in path:
        if rng.next_float() >= width_chance:
            continue
        options = [n for n in hex_neighbors(*tile) if n in tile_set and n not in tiles]
        if options:
            tiles.add(_pick(rng, options))
    return tiles


def carve_rivers(
    floor_seed: int,
    regions: dict[Tile, str],
    tiles: list[Tile],
    tile_set: set[Tile],
    radius: int,
    cfg: dict,
) -> set[Tile]:
    """Draw `count` rivers across the floor, returning every tile they cover.

    Sources sit on the outer ring and mouths near the opposite side, so a river
    spans the disc rather than nicking a corner. The bisection this causes is
    intentional and is left for the crossing carver to resolve.
    """
    rng = _stream(floor_seed, "rivers")
    biome = cfg["biome"]
    count = cfg["count"]
    meander = cfg["meander"]
    width_chance = cfg["width_chance"]
    ring = _ring(tiles, radius)
    if not ring:
        return set()

    covered: set[Tile] = set()
    for _ in range(count):
        source = _pick(rng, ring)
        target = _opposite(source)
        # The exact opposite tile may sit outside the disc after rounding, and
        # a little scatter keeps multiple rivers on one floor from running
        # parallel. Nearest legal ring tile to the antipode.
        mouth = min(ring, key=lambda t: (hex_distance(t, target), t))
        path = river_path(rng, tile_set, source, mouth, meander, max_steps=8 * radius)
        covered |= widen(rng, path, tile_set, width_chance)

    for tile in covered:
        regions[tile] = biome
    return covered


# --- Chambers -------------------------------------------------------------

def hex_room(center: Tile, room_radius: int, tile_set: set[Tile]) -> set[Tile]:
    """A radial clearing: every tile within `room_radius` of `center`.

    Rooms are hex discs rather than rectangles because the grid is hex -- a
    rectangular room on a hex grid has ragged edges that read as noise, not as
    architecture.
    """
    return {
        t for t in tile_set
        if hex_distance(center, t) <= room_radius
    }


def carve_chambers(
    floor_seed: int,
    regions: dict[Tile, str],
    tiles: list[Tile],
    tile_set: set[Tile],
    radius: int,
    structural: list[Tile],
    cfg: dict,
) -> set[Tile]:
    """Turn an open floor into rooms joined by corridors.

    Inverts the usual approach: rather than carving rooms out of solid rock,
    everything the rooms and corridors do *not* cover is overwritten with the
    wall biome. That keeps whatever the macro layout decided inside the rooms,
    so a chamber floor still varies by biome instead of being one flat colour,
    and it means this stage composes with all three layout modes rather than
    replacing them.

    Rooms are anchored on the structural tiles first (both staircases and the
    centre) so a staircase always opens into a room rather than a corridor
    stub, then filled out with `room_count` further rooms placed with minimum
    spacing. Corridors join consecutive room centres in placement order, which
    guarantees the whole room graph is connected as a chain -- the crossing
    carver would fix a disconnected one, but it would do it by tunnelling
    through walls in a straight line, and a corridor that ignores the room
    layout looks exactly like the bug it isn't.
    """
    rng = _stream(floor_seed, "chambers")
    wall = cfg["wall_biome"]
    room_min, room_max = cfg["room_radius_min"], cfg["room_radius_max"]
    spacing = cfg["min_spacing"]

    centers: list[Tile] = [t for t in structural if t in tile_set]
    # Candidates are drawn from an inset disc: a room centred on the rim is
    # mostly outside the floor, so it carves a sliver instead of a chamber.
    inset = max(1, int(radius * cfg["inset_pct"]))
    pool = [t for t in tiles if hex_distance(CENTER, t) <= radius - inset]

    for _ in range(cfg["room_count"]):
        legal = [t for t in pool if all(hex_distance(t, c) >= spacing for c in centers)]
        if not legal:
            break
        centers.append(_pick(rng, legal))

    open_tiles: set[Tile] = set()
    for center in centers:
        span = room_min + int(rng.next_float() * (room_max - room_min + 1))
        open_tiles |= hex_room(center, min(span, room_max), tile_set)

    for a, b in zip(centers, centers[1:]):
        corridor = find_path(a, b, lambda t: t in tile_set)
        if corridor:
            open_tiles |= set(corridor)

    for tile in tiles:
        if tile not in open_tiles:
            regions[tile] = wall
    return open_tiles
