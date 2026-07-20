"""Deterministic map generation (compendium §4.1, §11.1).

Server-authoritative: the layout (regions, roads, structural fields) is
generated here and shipped whole in the floor snapshot. The client renders it
and never regenerates -- so there is no cross-language determinism to keep.

Scope is deliberately narrow: this module handles the physical canvas only
(terrain shape, elevation, biome regions, roads/exits) and nothing about what
populates it. Monster and resource placement lives in gep/spawner.py, reads
this module's output as metadata (regions/elevation/roughness/roads), and
runs off its own independent seed -- see that module's docstring for why the
two are kept apart. FloorLayout carries no entity data at all.

Two generation paths:
  - New (archetypes + biomes provided): floor archetype chosen from the floor
    number (e.g. every 25th floor is a safe town), then that archetype's
    configured generation pipeline is run.
  - Legacy (archetypes/biomes omitted): the original flat ruleset behaviour,
    kept for unit tests that construct a ruleset directly.

This module owns only the *framing* of generation -- seeding, radius, exit
placement, and packing the result into a FloorLayout. The generation steps
themselves live in gep/pipeline.py and are composed by config
(`archetypes.<name>.pipeline`), so this file has no opinion about what a
floor is made of or in what order.

Hard constraints (gep/constraints.py) still run at the end of the template
path -- exits reachable from spawn, no template-forbidden biome pair adjacent
-- and pipeline.py appends them unconditionally so no config can skip them.
A violation is either repaired in place or raises GenerationError, never
shipped silently.
"""
from dataclasses import dataclass, field

from gep.hexgrid import ring_tiles, tiles_in_radius
from gep.pipeline import GenContext, run_pipeline
from gep.prefabs import PlacedPrefab
from gep.prng import Mulberry32, seed_from_floor
from gep.tiles import STAIRS_DOWN, STAIRS_UP

Tile = tuple[int, int]


@dataclass
class FloorLayout:
    tower_id: str
    floor_number: int
    radius: int
    tiles: list[Tile]
    up_exit: Tile
    down_exit: Tile | None
    regions: dict[Tile, str] = field(default_factory=dict)
    roads: set[Tile] = field(default_factory=set)
    archetype: str = "dungeon"
    safe: bool = False
    # Per-tile structural fields in [0, 1]; empty on the legacy path.
    elevation: dict[Tile, float] = field(default_factory=dict)
    roughness: dict[Tile, float] = field(default_factory=dict)
    # Fixed-footprint structures stamped onto the floor (gep/prefabs.py);
    # empty on the legacy path and whenever the archetype names no prefabs.
    prefabs: list[PlacedPrefab] = field(default_factory=list)
    # Reserved tile types (gep/tiles.py), keyed by tile. Sparse: only the
    # handful of tiles that carry a structural identity appear here, so this
    # ships as a plain dict rather than a packed per-tile array.
    tile_types: dict[Tile, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tower_id": self.tower_id,
            "floor_number": self.floor_number,
            "radius": self.radius,
            "up_exit": list(self.up_exit),
            "down_exit": list(self.down_exit) if self.down_exit else None,
            "archetype": self.archetype,
            "safe": self.safe,
            "regions": {f"{q},{r}": bid for (q, r), bid in self.regions.items()},
            "roads": [list(t) for t in sorted(self.roads)],
            "tile_types": {f"{q},{r}": t for (q, r), t in self.tile_types.items()},
            "prefabs": [
                {
                    "prefab_id": p.prefab_id,
                    "anchor": list(p.anchor),
                    "tiles": [list(t) for t in p.tiles],
                    "tile_sprites": {f"{q},{r}": s for (q, r), s in p.tile_sprites.items()},
                }
                for p in self.prefabs
            ],
        }


def _floor_index(rng: Mulberry32, n: int) -> int:
    return min(n - 1, int(rng.next_float() * n))


# --- Compact wire packing -------------------------------------------------
# Per-tile fields ship as base64 byte arrays in canonical tile order (the
# order of tiles_in_radius), not as 12k-entry JSON objects. The client walks
# the same order to rebuild its lookup.

def pack_unit_field(field: dict[Tile, float], tiles: list[Tile]) -> str:
    """Quantise a [0, 1] field to one byte per tile, base64 encoded."""
    import base64
    buf = bytearray(len(tiles))
    for i, tile in enumerate(tiles):
        v = field.get(tile, 0.5)
        buf[i] = max(0, min(255, int(round(v * 255.0))))
    return base64.b64encode(bytes(buf)).decode("ascii")


def pack_biome_map(regions: dict[Tile, str], tiles: list[Tile], legend: list[str]) -> str:
    """One byte per tile indexing into `legend`, base64 encoded."""
    import base64
    index = {bid: i for i, bid in enumerate(legend)}
    buf = bytearray(len(tiles))
    for i, tile in enumerate(tiles):
        buf[i] = index.get(regions.get(tile), 0)
    return base64.b64encode(bytes(buf)).decode("ascii")


def resolve_archetype(floor_number: int, archetypes: dict) -> tuple[str, dict]:
    """Pick the archetype id + its params for a floor number.

    First matching override wins, so overrides are listed most-specific
    first: floor 175 is a multiple of both 25 and 7 and must resolve to the
    town, not the fungal cavern.
    """
    name = archetypes.get("default_archetype", "dungeon")
    for rule in archetypes.get("overrides", []):
        m = rule.get("floor_multiple_of")
        if m and floor_number % m == 0:
            name = rule["archetype"]
            break
    params = archetypes["archetypes"][name]
    return name, params


def _place_exits(floor_rng: Mulberry32, radius: int, floor_number: int) -> tuple[Tile, Tile | None]:
    ring_pool = ring_tiles(radius, radius)
    up_exit = ring_pool.pop(_floor_index(floor_rng, len(ring_pool)))
    down_exit = None
    if floor_number > 1:
        down_exit = ring_pool.pop(_floor_index(floor_rng, len(ring_pool)))
    return up_exit, down_exit


def generate_floor(
    tower_id: str,
    floor_number: int,
    global_seed: str,
    ruleset: dict,
    archetypes: dict | None = None,
    biomes: dict | None = None,
    prefabs: dict | None = None,
) -> FloorLayout:
    floor_seed = seed_from_floor(tower_id, floor_number, global_seed)
    floor_rng = Mulberry32(floor_seed)

    radius = ruleset["radius"]
    all_tiles = tiles_in_radius(radius)
    up_exit, down_exit = _place_exits(floor_rng, radius, floor_number)

    # The exits are not just coordinates the client happens to colour in: they
    # are tiles with an identity, assigned here at the moment they are chosen
    # so there is exactly one place that decides where stairs are.
    tile_types = {up_exit: STAIRS_UP}
    if down_exit:
        tile_types[down_exit] = STAIRS_DOWN

    reserved = {up_exit}
    if down_exit:
        reserved.add(down_exit)

    # ---- Legacy path: flat ruleset, no regions/roads ----
    if archetypes is None or biomes is None:
        return FloorLayout(
            tower_id=tower_id, floor_number=floor_number, radius=radius, tiles=all_tiles,
            up_exit=up_exit, down_exit=down_exit, tile_types=tile_types,
        )

    # ---- Template path: hierarchical generation ----
    archetype_name, params = resolve_archetype(floor_number, archetypes)
    safe = params.get("safe", False)

    # The sequence of generation steps is data, not code: the archetype's
    # `pipeline` array names them in order and gep/pipeline.py maps each id to
    # its function. Path carving and connectivity validation are appended by
    # run_pipeline regardless of config -- see that module on why those two
    # are not the config author's to schedule.
    ctx = GenContext(
        floor_seed=floor_seed, radius=radius, tiles=all_tiles, tile_set=set(all_tiles),
        up_exit=up_exit, down_exit=down_exit, params=params, prefab_defs=prefabs,
    )
    run_pipeline(ctx, params["pipeline"])

    return FloorLayout(
        tower_id=tower_id, floor_number=floor_number, radius=radius, tiles=all_tiles,
        up_exit=up_exit, down_exit=down_exit,
        regions=ctx.regions, roads=ctx.roads, archetype=archetype_name, safe=safe,
        elevation=ctx.elevation, roughness=ctx.roughness, prefabs=ctx.prefabs,
        tile_types=tile_types,
    )
