"""Deterministic floor generation (compendium §4.1, §11.1).

Server-authoritative: the layout (regions, roads, resources, monster spawns)
is generated here and shipped whole in the floor snapshot. The client renders
it and never regenerates -- so there is no cross-language determinism to keep.

Two generation paths:
  - New (archetypes + biomes provided): floor archetype chosen from the floor
    number (e.g. every 25th floor is a safe town), Voronoi biome regions,
    a road spine between the floor's entrance and exit, per-biome resource and
    monster spawns, and monster placement biased off-road (roads are safer).
  - Legacy (archetypes/biomes omitted): the original flat ruleset behaviour,
    kept for unit tests that construct a ruleset directly.
"""
from dataclasses import dataclass, field

from gep.hexgrid import ring_tiles, tiles_in_radius
from gep.prng import Mulberry32, rng_for_tile, seed_from_floor
from gep.regions import assign_regions
from gep.roads import build_roads
from gep.rolls import weighted_choice

Tile = tuple[int, int]


@dataclass
class FloorLayout:
    tower_id: str
    floor_number: int
    radius: int
    tiles: list[Tile]
    up_exit: Tile
    down_exit: Tile | None
    resource_nodes: dict[Tile, str] = field(default_factory=dict)
    monster_spawns: list[dict] = field(default_factory=list)
    regions: dict[Tile, str] = field(default_factory=dict)
    roads: set[Tile] = field(default_factory=set)
    archetype: str = "dungeon"
    safe: bool = False

    def to_dict(self) -> dict:
        return {
            "tower_id": self.tower_id,
            "floor_number": self.floor_number,
            "radius": self.radius,
            "up_exit": list(self.up_exit),
            "down_exit": list(self.down_exit) if self.down_exit else None,
            "archetype": self.archetype,
            "safe": self.safe,
            "resource_nodes": {f"{q},{r}": rid for (q, r), rid in self.resource_nodes.items()},
            "monster_spawns": [
                {"tile": list(s["tile"]), "template_id": s["template_id"]}
                for s in self.monster_spawns
            ],
            "regions": {f"{q},{r}": bid for (q, r), bid in self.regions.items()},
            "roads": [list(t) for t in sorted(self.roads)],
        }


def _floor_index(rng: Mulberry32, n: int) -> int:
    return min(n - 1, int(rng.next_float() * n))


def resolve_archetype(floor_number: int, archetypes: dict) -> tuple[str, dict]:
    """Pick the archetype id + its params for a floor number."""
    name = archetypes.get("default_archetype", "dungeon")
    for rule in archetypes.get("overrides", []):
        m = rule.get("floor_multiple_of")
        if m and floor_number % m == 0:
            name = rule["archetype"]
    params = archetypes["archetypes"][name]
    return name, params


def _place_exits(floor_rng: Mulberry32, radius: int, floor_number: int) -> tuple[Tile, Tile | None]:
    ring_pool = ring_tiles(radius, radius)
    up_exit = ring_pool.pop(_floor_index(floor_rng, len(ring_pool)))
    down_exit = None
    if floor_number > 1:
        down_exit = ring_pool.pop(_floor_index(floor_rng, len(ring_pool)))
    return up_exit, down_exit


def _weighted_sample_tiles(
    floor_rng: Mulberry32, weighted: list[tuple[Tile, float]], count: int
) -> list[Tile]:
    """Sample `count` tiles without replacement, proportional to weight."""
    pool = [wt for wt in weighted if wt[1] > 0]
    picked: list[Tile] = []
    for _ in range(min(count, len(pool))):
        total = sum(w for _, w in pool)
        if total <= 0:
            break
        roll = floor_rng.next_float() * total
        cumulative = 0.0
        chosen_idx = len(pool) - 1
        for i, (_, w) in enumerate(pool):
            cumulative += w
            if roll < cumulative:
                chosen_idx = i
                break
        picked.append(pool.pop(chosen_idx)[0])
    return picked


def generate_floor(
    tower_id: str,
    floor_number: int,
    global_seed: str,
    ruleset: dict,
    archetypes: dict | None = None,
    biomes: dict | None = None,
) -> FloorLayout:
    floor_seed = seed_from_floor(tower_id, floor_number, global_seed)
    floor_rng = Mulberry32(floor_seed)

    radius = ruleset["radius"]
    all_tiles = tiles_in_radius(radius)
    up_exit, down_exit = _place_exits(floor_rng, radius, floor_number)

    reserved = {up_exit}
    if down_exit:
        reserved.add(down_exit)

    # ---- Legacy path: flat ruleset weights, no regions/roads ----
    if archetypes is None or biomes is None:
        resource_nodes: dict[Tile, str] = {}
        chance = ruleset["resource_spawn_chance"]
        weights = ruleset["resource_weights"]
        for tile in all_tiles:
            if tile in reserved:
                continue
            q, r = tile
            tile_rng = rng_for_tile(floor_seed, q, r)
            if tile_rng.next_float() < chance:
                resource_nodes[tile] = weighted_choice(tile_rng, weights)

        available = [t for t in all_tiles if t not in reserved and t not in resource_nodes]
        spawns: list[dict] = []
        for _ in range(min(ruleset["monster_spawn_count"], len(available))):
            idx = _floor_index(floor_rng, len(available))
            tile = available.pop(idx)
            spawns.append({"tile": tile, "template_id": weighted_choice(floor_rng, ruleset["monster_weights"])})

        return FloorLayout(
            tower_id=tower_id, floor_number=floor_number, radius=radius, tiles=all_tiles,
            up_exit=up_exit, down_exit=down_exit,
            resource_nodes=resource_nodes, monster_spawns=spawns,
        )

    # ---- New path: archetype -> regions -> roads -> spawns ----
    archetype_name, params = resolve_archetype(floor_number, archetypes)
    safe = params.get("safe", False)

    regions = assign_regions(
        floor_seed, all_tiles, params["region_count"], params["biome_weights"]
    )
    tile_set = set(all_tiles)
    roads = build_roads(tile_set, up_exit, down_exit)

    # Resources: per-tile chance and weights from the tile's biome.
    resource_nodes = {}
    for tile in all_tiles:
        if tile in reserved:
            continue
        biome = biomes[regions[tile]]
        chance = biome["resource_spawn_chance"]
        if chance <= 0 or not biome["resource_weights"]:
            continue
        q, r = tile
        tile_rng = rng_for_tile(floor_seed, q, r)
        if tile_rng.next_float() < chance:
            resource_nodes[tile] = weighted_choice(tile_rng, biome["resource_weights"])

    # Monsters: weight candidate tiles by biome danger, discounted on roads
    # (off-road is more dangerous), then pick each monster's template from its
    # tile's biome.
    spawns = []
    monster_count = params.get("monster_spawn_count", 0)
    if not safe and monster_count > 0:
        road_factor = ruleset.get("road_danger_factor", 0.1)
        candidates: list[tuple[Tile, float]] = []
        for tile in all_tiles:
            if tile in reserved or tile in resource_nodes:
                continue
            biome = biomes[regions[tile]]
            w = biome.get("monster_weight", 0.0)
            if w <= 0 or not biome["monster_weights"]:
                continue
            if tile in roads:
                w *= road_factor
            candidates.append((tile, w))

        for tile in _weighted_sample_tiles(floor_rng, candidates, monster_count):
            biome = biomes[regions[tile]]
            template_id = weighted_choice(floor_rng, biome["monster_weights"])
            spawns.append({"tile": tile, "template_id": template_id})

    return FloorLayout(
        tower_id=tower_id, floor_number=floor_number, radius=radius, tiles=all_tiles,
        up_exit=up_exit, down_exit=down_exit,
        resource_nodes=resource_nodes, monster_spawns=spawns,
        regions=regions, roads=roads, archetype=archetype_name, safe=safe,
    )
