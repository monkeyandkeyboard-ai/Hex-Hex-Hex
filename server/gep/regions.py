"""Voronoi biome regions over a hex floor.

Deterministic given the floor seed: pick N region seed tiles, assign each a
biome by weighted roll, then every tile joins the nearest seed (hex distance,
ties broken by seed index). Runs server-side only -- the resulting tile->biome
map ships in the floor snapshot, so the client never regenerates it.
"""
from gep.hexgrid import hex_distance
from gep.prng import Mulberry32, fnv1a32
from gep.rolls import weighted_choice

Tile = tuple[int, int]


def _region_rng(floor_seed: int) -> Mulberry32:
    # Independent stream from tile/monster RNGs so adding regions doesn't
    # shift any other per-floor roll.
    return Mulberry32(fnv1a32(f"{floor_seed}:regions".encode("utf-8")))


def assign_regions(
    floor_seed: int,
    tiles: list[Tile],
    region_count: int,
    biome_weights: list[tuple[str, float]],
) -> dict[Tile, str]:
    """Returns tile -> biome_id for every tile on the floor."""
    if not tiles or region_count <= 0 or not biome_weights:
        return {}

    rng = _region_rng(floor_seed)
    n = min(region_count, len(tiles))

    # Pick n distinct seed tiles.
    seed_tiles: list[Tile] = []
    chosen: set[Tile] = set()
    attempts = 0
    while len(seed_tiles) < n and attempts < n * 20:
        idx = int(rng.next_float() * len(tiles))
        if idx >= len(tiles):
            idx = len(tiles) - 1
        t = tiles[idx]
        if t not in chosen:
            chosen.add(t)
            seed_tiles.append(t)
        attempts += 1

    # Assign each seed a biome.
    seed_biomes = [weighted_choice(rng, biome_weights) for _ in seed_tiles]

    # Every tile joins its nearest seed (ties -> lowest seed index).
    regions: dict[Tile, str] = {}
    for tile in tiles:
        best_i, best_d = 0, None
        for i, seed in enumerate(seed_tiles):
            d = hex_distance(tile, seed)
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        regions[tile] = seed_biomes[best_i]
    return regions
