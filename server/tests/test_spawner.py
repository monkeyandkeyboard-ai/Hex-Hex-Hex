"""The spawner is a separate pipeline from map generation (gep/floorgen.py):
it reads a FloorLayout's biome/road metadata and populates it off its own
independent seed, per config/spawn_ruleset.json.
"""
import pathlib

from gep.config_loader import ConfigStore
from gep.floorgen import generate_floor
from gep.spawner import spawn_floor

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _spawn(floor_number, spawn_seed="test-spawn"):
    layout = generate_floor(
        "tower-a", floor_number, cfg.world["global_seed"],
        cfg.floor_ruleset, cfg.floor_archetypes, cfg.biomes,
    )
    return layout, spawn_floor(layout, cfg.biomes, cfg.spawn_ruleset, spawn_seed)


def test_spawning_is_deterministic():
    _, a = _spawn(6)
    _, b = _spawn(6)
    assert a.resource_nodes == b.resource_nodes
    assert a.monster_spawns == b.monster_spawns


def test_changing_spawn_seed_changes_spawns_not_layout():
    layout_a, plan_a = _spawn(6, spawn_seed="seed-one")
    layout_b, plan_b = _spawn(6, spawn_seed="seed-two")
    assert layout_a.regions == layout_b.regions
    assert layout_a.elevation == layout_b.elevation
    assert plan_a.monster_spawns != plan_b.monster_spawns


def test_safe_town_gets_no_monsters():
    layout, plan = _spawn(25)
    assert layout.safe is True
    assert plan.monster_spawns == []


def test_dungeon_floor_has_monsters_biased_off_road():
    layout, plan = _spawn(6)
    assert len(plan.monster_spawns) > 0
    on_road = sum(1 for s in plan.monster_spawns if tuple(s["tile"]) in layout.roads)
    assert on_road <= len(plan.monster_spawns) // 2


def test_spawns_never_land_on_exits():
    layout, plan = _spawn(6)
    reserved = {layout.up_exit, layout.down_exit}
    for tile in plan.resource_nodes:
        assert tile not in reserved
    for spawn in plan.monster_spawns:
        assert tuple(spawn["tile"]) not in reserved


def test_no_tile_is_both_resource_and_monster():
    _, plan = _spawn(6)
    monster_tiles = {tuple(s["tile"]) for s in plan.monster_spawns}
    assert not (monster_tiles & plan.resource_nodes.keys())


# --- Gathering categories -------------------------------------------------
# Resources come in three families -- minerals, herbs and trees -- each bound
# to its own non-combat skill. The spawner itself stays category-blind (it
# rolls whatever a biome weights), so these tests pin the two things that
# actually make the varieties real: that the distribution rules put each
# family in the biomes that should carry it, and that the placement stays
# deterministic now that a biome's weight list spans several categories.

def _category_of(resource_id):
    return cfg.resources[resource_id]["category"]


def test_every_biome_weights_only_resources_that_exist_in_a_known_category():
    for biome_id, biome in cfg.biomes.items():
        for resource_id, _weight in biome["resource_weights"]:
            assert _category_of(resource_id) in cfg.resource_categories, (
                f"biome {biome_id} weights {resource_id}, whose category is "
                f"not declared in resource_categories.json"
            )


def test_each_gathering_category_is_reachable_somewhere_in_the_world():
    """A category nothing weights is a profession with no nodes to train on --
    it loads clean and is unplayable, which is the failure mode worth catching
    at test time rather than by wandering the tower looking for trees."""
    weighted = {
        _category_of(rid)
        for biome in cfg.biomes.values()
        for rid, _w in biome["resource_weights"]
    }
    declared = {c for c in cfg.resource_categories if not c.startswith("_")}
    assert declared == weighted


def test_biomes_carry_the_categories_their_terrain_implies():
    # Bare rock grows nothing woody; the fungal hollows and the scorched fault
    # each carry all three families.
    def cats(biome_id):
        return {_category_of(rid) for rid, _w in cfg.biomes[biome_id]["resource_weights"]}

    assert cats("fungal") == {"mineral", "herb", "tree"}
    assert cats("hazard") == {"mineral", "herb", "tree"}
    assert cats("rocky") == {"mineral", "herb"}


def test_multi_category_floors_still_spawn_deterministically():
    _, a = _spawn(7)
    _, b = _spawn(7)
    assert a.resource_nodes == b.resource_nodes
    # And the roll actually reaches past minerals -- a floor that only ever
    # produced ore would pass every determinism check above unchanged.
    placed = {_category_of(rid) for rid in a.resource_nodes.values()}
    assert len(placed) > 1


def test_nodes_only_land_in_biomes_that_weight_their_resource():
    layout, plan = _spawn(7)
    for tile, resource_id in plan.resource_nodes.items():
        allowed = {rid for rid, _w in cfg.biomes[layout.regions[tile]]["resource_weights"]}
        assert resource_id in allowed
