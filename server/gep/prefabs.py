"""Generic prefab placement: stamps a fixed footprint of tiles (a town, a
monster encampment, whatever comes next) onto a generated floor.

This is a separate step from gep/biome_layout.py's macro layout -- biomes
paint large continuous regions, prefabs are small fixed-shape structures
placed *within* a legal region. Every prefab is pure config
(server/config/prefabs/*.json): footprint shape, per-tile sprite ids, and a
named set of placement constraints. Adding a new prefab that only needs the
existing constraint vocabulary requires zero code changes here -- that
genericity is the point (reusability was the explicit design goal, not just
"a town and an encampment").

Determinism: one dedicated Mulberry32 stream per floor
(f"{floor_seed}:prefabs"), separate from every other stream, so prefab
placement can never perturb or be perturbed by exit placement, biome layout,
or spawning. Candidate legality (does this site satisfy the constraints) is
plain deterministic tile math -- no RNG involved, matching the module
docstring standard in gep/noise.py/gep/prng.py: only *which* legal candidate
wins is a sequential decision, so only that step draws from the stream.
"""
from dataclasses import dataclass, field

from gep.biome_layout import radial_fraction
from gep.pathfinding import hex_neighbors
from gep.hexgrid import hex_distance
from gep.prng import Mulberry32, fnv1a32
from gep.rolls import weighted_sample_without_replacement

Tile = tuple[int, int]


@dataclass
class PlacedPrefab:
    prefab_id: str
    anchor: Tile
    tiles: list[Tile]
    tile_sprites: dict[Tile, str] = field(default_factory=dict)


def _prefab_rng(floor_seed: int) -> Mulberry32:
    return Mulberry32(fnv1a32(f"{floor_seed}:prefabs".encode("utf-8")))


def _footprint_tiles(anchor: Tile, footprint: list[list[int]]) -> list[Tile]:
    aq, ar = anchor
    return [(aq + dq, ar + dr) for dq, dr in footprint]


def _required_biomes(required_biome) -> set[str] | None:
    if required_biome is None:
        return None
    if isinstance(required_biome, str):
        return {required_biome}
    return set(required_biome)


def _site_legal(
    anchor: Tile,
    footprint_tiles: list[Tile],
    layout,
    tile_set: set[Tile],
    placement: dict,
    placed_anchors: list[Tile],
) -> bool:
    for tile in footprint_tiles:
        if tile not in tile_set:
            return False

    required = _required_biomes(placement.get("required_biome"))
    if required is not None:
        for tile in footprint_tiles:
            if layout.regions.get(tile) not in required:
                return False

    if placement.get("forbid_road_adjacent"):
        for tile in footprint_tiles:
            if tile in layout.roads:
                return False
            for neighbor in hex_neighbors(*tile):
                if neighbor in layout.roads:
                    return False

    min_pct = placement.get("min_radial_pct")
    max_pct = placement.get("max_radial_pct")
    if min_pct is not None or max_pct is not None:
        frac = radial_fraction(anchor, layout.radius)
        if min_pct is not None and frac < min_pct:
            return False
        if max_pct is not None and frac > max_pct:
            return False

    min_elev = placement.get("min_elevation")
    max_elev = placement.get("max_elevation")
    if (min_elev is not None or max_elev is not None) and layout.elevation:
        e = layout.elevation.get(anchor)
        if e is not None:
            if min_elev is not None and e < min_elev:
                return False
            if max_elev is not None and e > max_elev:
                return False

    min_spacing = placement.get("min_spacing")
    if min_spacing is not None:
        for other in placed_anchors:
            if hex_distance(anchor, other) < min_spacing:
                return False

    return True


def place_prefabs(
    floor_seed: int,
    layout,
    prefab_defs: dict,
    archetype_prefab_ids: list[str],
) -> list[PlacedPrefab]:
    """Place every prefab named in `archetype_prefab_ids` (config order,
    deterministic) onto `layout`. A prefab may place fewer than its
    configured maximum -- possibly zero -- if no legal site remains; this is
    a normal outcome (e.g. "encampments aren't always present"), not an error.
    """
    if not archetype_prefab_ids:
        return []

    rng = _prefab_rng(floor_seed)
    placed: list[PlacedPrefab] = []
    reserved_tiles: set[Tile] = set()
    placed_anchors: list[Tile] = []
    tile_set = set(layout.tiles)

    for prefab_id in archetype_prefab_ids:
        prefab_def = prefab_defs[prefab_id]
        footprint = prefab_def["footprint"]
        placement = prefab_def.get("placement", {})
        tile_sprites_cfg = prefab_def.get("tile_sprites", {})
        count_cfg = prefab_def.get("count", {"min": 1, "max": 1})

        count = count_cfg["min"]
        span = count_cfg["max"] - count_cfg["min"]
        if span > 0:
            count = count_cfg["min"] + min(span, int(rng.next_float() * (span + 1)))

        for _ in range(count):
            candidates: list[tuple[Tile, float]] = []
            for anchor in layout.tiles:
                footprint_tiles = _footprint_tiles(anchor, footprint)
                if any(t in reserved_tiles for t in footprint_tiles):
                    continue
                if not _site_legal(anchor, footprint_tiles, layout, tile_set, placement, placed_anchors):
                    continue
                candidates.append((anchor, 1.0))

            if not candidates:
                break

            chosen = weighted_sample_without_replacement(rng, candidates, 1)
            if not chosen:
                break
            anchor = chosen[0]
            footprint_tiles = _footprint_tiles(anchor, footprint)
            tile_sprites: dict[Tile, str] = {}
            for offset_key, sprite in tile_sprites_cfg.items():
                dq, dr = (int(part) for part in offset_key.split(","))
                tile_sprites[(anchor[0] + dq, anchor[1] + dr)] = sprite
            placed.append(PlacedPrefab(
                prefab_id=prefab_id, anchor=anchor,
                tiles=footprint_tiles, tile_sprites=tile_sprites,
            ))
            reserved_tiles.update(footprint_tiles)
            placed_anchors.append(anchor)

    return placed
