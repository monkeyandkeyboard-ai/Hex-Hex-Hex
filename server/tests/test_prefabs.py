"""Prefab placement (gep/prefabs.py) is a separate step from map generation
and spawning: fixed-footprint structures stamped onto a finished FloorLayout,
per the archetype's `prefabs` list and config/prefabs/*.json.
"""
import pathlib

from gep.config_loader import ConfigStore
from gep.floorgen import generate_floor

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _gen(floor_number, global_seed="test-prefabs"):
    return generate_floor(
        "tower-a", floor_number, global_seed,
        cfg.floor_ruleset, cfg.floor_archetypes, cfg.biomes,
        prefabs=cfg.prefabs,
    )


def test_prefab_placement_is_deterministic():
    a = _gen(25)
    b = _gen(25)
    a_placed = [(p.prefab_id, p.anchor, tuple(p.tiles)) for p in a.prefabs]
    b_placed = [(p.prefab_id, p.anchor, tuple(p.tiles)) for p in b.prefabs]
    assert a_placed == b_placed


def test_town_floor_always_gets_a_market():
    layout = _gen(25)
    assert layout.safe is True
    ids = [p.prefab_id for p in layout.prefabs]
    assert ids.count("town_market") == 1


def test_market_tiles_are_all_town_biome():
    layout = _gen(25)
    market = next(p for p in layout.prefabs if p.prefab_id == "town_market")
    for tile in market.tiles:
        assert layout.regions[tile] == "town"


def test_encampment_never_borders_a_road():
    from gep.pathfinding import hex_neighbors

    layout = _gen(6)
    encampments = [p for p in layout.prefabs if p.prefab_id == "monster_encampment"]
    for camp in encampments:
        for tile in camp.tiles:
            assert tile not in layout.roads
            for neighbor in hex_neighbors(*tile):
                assert neighbor not in layout.roads


def test_encampments_respect_min_spacing():
    layout = _gen(6)
    encampments = [p for p in layout.prefabs if p.prefab_id == "monster_encampment"]
    from gep.hexgrid import hex_distance
    for i, a in enumerate(encampments):
        for b in encampments[i + 1:]:
            assert hex_distance(a.anchor, b.anchor) >= 8


def test_no_prefabs_when_archetype_has_none():
    # Legacy/no-prefab path: generate_floor without a `prefabs` registry at
    # all must not error and must leave the layout's prefab list empty.
    layout = generate_floor(
        "tower-a", 6, "test-prefabs",
        cfg.floor_ruleset, cfg.floor_archetypes, cfg.biomes,
    )
    assert layout.prefabs == []
