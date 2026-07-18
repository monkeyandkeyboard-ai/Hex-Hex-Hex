"""Server-side generation: regions, roads, and floor archetypes.

Generation is server-authoritative (the layout ships in the snapshot), so
these tests assert the Python output directly rather than cross-checking a
client reimplementation.
"""
import pathlib

from gep.config_loader import ConfigStore
from gep.floorgen import generate_floor, resolve_archetype
from gep.pathfinding import hex_neighbors

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _gen(floor_number):
    return generate_floor(
        "tower-a", floor_number, cfg.world["global_seed"],
        cfg.floor_ruleset, cfg.floor_archetypes, cfg.biomes,
    )


def test_generation_is_deterministic():
    a = _gen(7).to_dict()
    b = _gen(7).to_dict()
    assert a == b


def test_regions_cover_every_tile_with_valid_biomes():
    layout = _gen(3)
    assert len(layout.regions) == len(layout.tiles)
    for biome_id in layout.regions.values():
        assert biome_id in cfg.biomes


def test_road_connects_entrance_to_exit():
    layout = _gen(5)  # has both up and down exits
    assert layout.up_exit in layout.roads
    assert layout.down_exit in layout.roads
    # Road tiles form a connected chain (each has a road neighbor).
    for tile in layout.roads:
        if len(layout.roads) == 1:
            break
        assert any(tuple(n) in layout.roads for n in hex_neighbors(*tile))


def test_floor_1_road_starts_at_center():
    layout = _gen(1)
    assert layout.down_exit is None
    assert (0, 0) in layout.roads
    assert layout.up_exit in layout.roads


def test_every_25th_floor_is_a_safe_town():
    name, params = resolve_archetype(25, cfg.floor_archetypes)
    assert name == "town"
    town = _gen(25)
    assert town.safe is True
    assert town.monster_spawns == []
    assert town.archetype == "town"


def test_dungeon_floor_has_monsters_biased_off_road():
    layout = _gen(6)
    assert len(layout.monster_spawns) > 0
    on_road = sum(1 for s in layout.monster_spawns if tuple(s["tile"]) in layout.roads)
    # Off-road danger: with road_danger_factor 0.1, most spawns avoid roads.
    assert on_road <= len(layout.monster_spawns) // 2
