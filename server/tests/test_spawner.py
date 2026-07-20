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
