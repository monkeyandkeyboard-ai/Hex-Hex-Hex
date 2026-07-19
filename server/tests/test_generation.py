"""Server-side generation: regions, roads, and floor archetypes.

Generation is server-authoritative (the layout ships in the snapshot), so
these tests assert the Python output directly rather than cross-checking a
client reimplementation.
"""
import base64
import pathlib

from gep.config_loader import ConfigStore
from gep.floorgen import (
    generate_floor, pack_biome_map, pack_unit_field, resolve_archetype,
)
from gep.hexgrid import hex_distance
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
    assert name == "town_hub"
    town = _gen(25)
    assert town.safe is True
    assert town.monster_spawns == []
    assert town.archetype == "town_hub"


def test_first_matching_override_wins():
    # 175 is a multiple of both 25 (town) and 7 (fungal); town is listed
    # first and must win.
    name, _ = resolve_archetype(175, cfg.floor_archetypes)
    assert name == "town_hub"


# --- Template system ------------------------------------------------------

def test_town_core_is_town_biome_and_flattened():
    layout = _gen(25)
    R = layout.radius
    core = [t for t in layout.tiles if hex_distance((0, 0), t) <= 0.30 * R]
    assert {layout.regions[t] for t in core} == {"town"}
    # Elevation clamped flat inside the core -- no peaks through the village.
    core_elev = {round(layout.elevation[t], 6) for t in core}
    assert core_elev == {0.5}


def test_town_floor_forbids_hazard_entirely():
    layout = _gen(25)
    assert "hazard" not in set(layout.regions.values())


def test_cluster_radial_constraint_keeps_hazard_out_of_the_centre():
    layout = _gen(1)  # rocky_depths: hazard min_radius_pct 0.45
    hazard = [t for t in layout.tiles if layout.regions[t] == "hazard"]
    assert hazard, "expected some hazard cells on a rocky_depths floor"
    closest = min(hex_distance((0, 0), t) for t in hazard) / layout.radius
    assert closest >= 0.44  # cells anchor beyond the limit


def test_elevation_layout_puts_fungal_in_the_lows():
    layout = _gen(7)  # fungal_cavern: strata below 0.42 -> fungal
    fungal = [layout.elevation[t] for t in layout.tiles if layout.regions[t] == "fungal"]
    rocky = [layout.elevation[t] for t in layout.tiles if layout.regions[t] == "rocky"]
    assert fungal and rocky
    assert max(fungal) <= 0.42 + 1e-9
    assert sum(fungal) / len(fungal) < sum(rocky) / len(rocky)


def test_structural_fields_cover_every_tile_in_unit_range():
    layout = _gen(6)
    assert len(layout.elevation) == len(layout.tiles)
    assert len(layout.roughness) == len(layout.tiles)
    for v in list(layout.elevation.values()) + list(layout.roughness.values()):
        assert 0.0 <= v <= 1.0


def test_packed_payload_round_trips_in_canonical_order():
    layout = _gen(6)
    legend = sorted(cfg.biomes.keys())
    packed = pack_biome_map(layout.regions, layout.tiles, legend)
    raw = base64.b64decode(packed)
    assert len(raw) == len(layout.tiles)
    # Every byte decodes back to the biome the generator assigned.
    for i, tile in enumerate(layout.tiles):
        assert legend[raw[i]] == layout.regions[tile]


def test_packed_elevation_quantises_within_one_step():
    layout = _gen(6)
    raw = base64.b64decode(pack_unit_field(layout.elevation, layout.tiles))
    for i, tile in enumerate(layout.tiles):
        assert abs(raw[i] / 255.0 - layout.elevation[tile]) <= 1 / 255.0


def test_dungeon_floor_has_monsters_biased_off_road():
    layout = _gen(6)
    assert len(layout.monster_spawns) > 0
    on_road = sum(1 for s in layout.monster_spawns if tuple(s["tile"]) in layout.roads)
    # Off-road danger: with road_danger_factor 0.1, most spawns avoid roads.
    assert on_road <= len(layout.monster_spawns) // 2
