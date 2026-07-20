"""Unified entity spawner: populates a generated FloorLayout with resource
nodes and monsters.

This is deliberately a separate pipeline from gep/floorgen.py. The map
generator's floor_seed governs terrain only; it must stay free to be re-run,
re-tuned, or replaced without ever perturbing what spawns where, and the
spawner's own ruleset (density, weights, which biomes/elevations/roads are
eligible) must be re-tunable without perturbing the terrain. The two seeds
(prng.seed_from_floor for the map, prng.seed_for_spawner here) are unrelated
inputs, not one derived from the other.

The spawner treats the layout purely as read-only metadata: `regions` (biome
per tile), `elevation`/`roughness` (structural fields), `roads` (membership),
and `archetype`/`safe`. It never looks at the layout's *seed*, only its
*shape*. A single unified pass places both resources and monsters together
(rather than two independent passes) so the two can't double-book a tile.
"""
from dataclasses import dataclass, field

from gep.floorgen import FloorLayout
from gep.hexgrid import hex_distance
from gep.prng import Mulberry32, rng_for_tile, seed_for_spawner
from gep.rolls import weighted_choice, weighted_sample_without_replacement

Tile = tuple[int, int]


@dataclass
class SpawnPlan:
    resource_nodes: dict[Tile, str] = field(default_factory=dict)
    monster_spawns: list[dict] = field(default_factory=list)


def _nearest_prefab_distance(tile: Tile, prefab_tiles: list[Tile]) -> int:
    return min(hex_distance(tile, pt) for pt in prefab_tiles)


def spawn_floor(
    layout: FloorLayout,
    biomes: dict,
    spawn_ruleset: dict,
    spawn_seed: str,
    prefabs: dict | None = None,
) -> SpawnPlan:
    """Populate `layout` with resource nodes and monster spawns.

    `biomes` supplies each biome's own spawn weights/chances -- content data,
    not the map generator's seed. `spawn_ruleset` supplies the spawner's
    cross-biome tuning knobs (per-archetype monster counts, road danger
    discount). `spawn_seed` is the world's independent spawn seed string
    (world.json's `spawn_seed`, distinct from `global_seed`). `prefabs` is the
    prefab id -> config registry (ConfigStore.prefabs); only consulted for
    each placed prefab's optional `effects` block (e.g. an encampment biasing
    nearby monster density/rarity) -- prefab *placement* itself is entirely
    gep/prefabs.py's concern, this module only reads where prefabs ended up.
    """
    reserved = {layout.up_exit}
    if layout.down_exit:
        reserved.add(layout.down_exit)

    root_seed = seed_for_spawner(layout.tower_id, layout.floor_number, spawn_seed)

    if not layout.regions:
        # Legacy/physical-only layout with no biome metadata: nothing to
        # read rules from, so nothing spawns. Content-bearing floors always
        # carry regions (see floorgen's template path).
        return SpawnPlan()

    # Resources: per-tile chance and weights from the tile's biome, rolled
    # independently per tile so tile iteration order can't affect outcomes.
    resource_nodes: dict[Tile, str] = {}
    for tile in layout.tiles:
        if tile in reserved:
            continue
        biome = biomes[layout.regions[tile]]
        chance = biome["resource_spawn_chance"]
        if chance <= 0 or not biome["resource_weights"]:
            continue
        q, r = tile
        tile_rng = rng_for_tile(root_seed, q, r)
        if tile_rng.next_float() < chance:
            resource_nodes[tile] = weighted_choice(tile_rng, biome["resource_weights"])

    # Monsters: weight candidate tiles by biome danger, discounted on roads
    # (off-road is more dangerous), then pick each monster's template from
    # its tile's biome. Count and road discount come from the spawner's own
    # ruleset, keyed by archetype -- density is a population decision, not a
    # terrain-template one.
    # Rarity/density prefab effects: only prefabs whose config declares an
    # `effects` block matter here, keyed generically off that block rather
    # than any particular prefab id, so a future prefab type gets the same
    # treatment for free just by adding one.
    prefab_effects: list[tuple[list[Tile], dict]] = []
    if prefabs:
        for placed in layout.prefabs:
            effects = prefabs.get(placed.prefab_id, {}).get("effects")
            if effects:
                prefab_effects.append((placed.tiles, effects))

    monster_spawns: list[dict] = []
    monster_count = spawn_ruleset.get("archetype_monster_counts", {}).get(layout.archetype, 0)
    if not layout.safe and monster_count > 0:
        spawn_rng = Mulberry32(root_seed ^ 0x4D0B)
        road_factor = spawn_ruleset.get("road_danger_factor", 0.1)
        candidates: list[tuple[Tile, float]] = []
        # tile -> reward_table id override, populated only for tiles inside a
        # prefab's rarity radius (e.g. an encampment's elite reward table).
        reward_overrides: dict[Tile, str] = {}
        for tile in layout.tiles:
            if tile in reserved or tile in resource_nodes:
                continue
            biome = biomes[layout.regions[tile]]
            w = biome.get("monster_weight", 0.0)
            if w <= 0 or not biome["monster_weights"]:
                continue
            if tile in layout.roads:
                w *= road_factor
            for prefab_tiles, effects in prefab_effects:
                radius = effects.get("monster_rarity_radius")
                if radius is None:
                    continue
                if _nearest_prefab_distance(tile, prefab_tiles) <= radius:
                    w *= effects.get("monster_weight_multiplier", 1.0)
                    override = effects.get("reward_table_override")
                    if override:
                        reward_overrides[tile] = override
            candidates.append((tile, w))

        for tile in weighted_sample_without_replacement(spawn_rng, candidates, monster_count):
            biome = biomes[layout.regions[tile]]
            template_id = weighted_choice(spawn_rng, biome["monster_weights"])
            spawn = {"tile": tile, "template_id": template_id}
            if tile in reward_overrides:
                spawn["reward_table_override"] = reward_overrides[tile]
            monster_spawns.append(spawn)

    return SpawnPlan(resource_nodes=resource_nodes, monster_spawns=monster_spawns)
