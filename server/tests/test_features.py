"""River and chamber feature stages (gep/features.py), and the guarantees the
crossing carver has to hold up once they exist.

The interesting property here is not that a river gets drawn -- it is that a
river is *allowed* to bisect the floor, because resolving that is the crossing
carver's job and not the river's. These tests pin that division of labour.
"""
import pytest

from gep.config_loader import ConfigError, ConfigStore
from gep.features import carve_rivers, hex_room, river_path, widen
from gep.floorgen import generate_floor
from gep.hexgrid import hex_distance, tiles_in_radius
from gep.passability import open_components, terrain_predicate
from gep.pathfinding import find_path, hex_neighbors
from gep.prng import Mulberry32

BIOMES = {
    "grass": {"passable": True},
    "water": {"passable": False},
    "wall": {"passable": False},
}
RULESET = {
    "radius": 20,
    "exit_separation": {"min_moves": 6, "max_diameter_pct": 0.9},
    "min_island_tiles": 25,
}

RIVERS = {"biome": "water", "count": 2, "meander": 0.35, "width_chance": 0.45}
CHAMBERS = {"wall_biome": "wall", "room_count": 8, "room_radius_min": 3,
            "room_radius_max": 6, "min_spacing": 7, "inset_pct": 0.12}


def _archetype(pipeline, **blocks):
    params = {
        "safe": False,
        "fallback_biome": "grass",
        "pipeline": pipeline,
        "layout": {"mode": "radial", "bands": [{"to": 1.01, "biome": "grass"}]},
        "elevation": {"octaves": 3, "scale": 0.08, "flatten": []},
        "roughness": {"octaves": 3, "scale": 0.2},
    }
    params.update(blocks)
    return {"default_archetype": "t", "overrides": [], "archetypes": {"t": params}}


def _gen(pipeline, floor=2, **blocks):
    return generate_floor(
        "tower-a", floor, "seed-1", RULESET,
        archetypes=_archetype(pipeline, **blocks), biomes=BIOMES,
    )


def _reachable(layout):
    open_tiles = set(layout.tiles) - layout.blocked
    start = layout.down_exit or (0, 0)
    seen, stack = {start}, [start]
    while stack:
        for n in hex_neighbors(*stack.pop()):
            if n in open_tiles and n not in seen:
                seen.add(n)
                stack.append(n)
    return seen, open_tiles


# --- River pathing ---------------------------------------------------------

def test_a_river_is_a_continuous_unbroken_path():
    """Every consecutive pair adjacent -- a river with a gap in it is two
    rivers, and the gap is a crossing nobody carved."""
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.35, 200)
    assert len(path) > 1
    for a, b in zip(path, path[1:]):
        assert hex_distance(a, b) == 1, f"gap between {a} and {b}"


def test_a_river_never_doubles_back_over_itself():
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.35, 200)
    assert len(set(path)) == len(path)


def test_a_river_reaches_its_mouth():
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.35, 200)
    assert path[0] == (-15, 0)
    assert path[-1] == (15, 0)


def test_a_river_stays_on_the_floor():
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.5, 200)
    assert all(t in tiles for t in path)


def test_meander_makes_the_river_wander():
    """A straight line is a canal. The meandering walk must be measurably
    longer than the shortest path between the same endpoints."""
    tiles = set(tiles_in_radius(15))
    straight = hex_distance((-15, 0), (15, 0))
    wandering = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.6, 400)
    assert len(wandering) - 1 > straight


def test_zero_meander_takes_the_direct_route():
    """Pins that the extra length above comes from `meander` and not from the
    walk being generally inefficient."""
    tiles = set(tiles_in_radius(15))
    direct = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.0, 400)
    assert len(direct) - 1 == hex_distance((-15, 0), (15, 0))


def test_widening_keeps_the_river_one_or_two_tiles_across():
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.35, 200)
    wide = widen(Mulberry32(11), path, tiles, 1.0)
    assert set(path) <= wide
    # Every added tile touches the spine, so the channel never exceeds 2 across.
    spine = set(path)
    for tile in wide - spine:
        assert any(n in spine for n in hex_neighbors(*tile))


def test_widening_is_optional_and_respects_its_chance():
    tiles = set(tiles_in_radius(15))
    path = river_path(Mulberry32(7), tiles, (-15, 0), (15, 0), 0.35, 200)
    assert widen(Mulberry32(11), path, tiles, 0.0) == set(path)


def test_rivers_are_deterministic():
    tiles = tiles_in_radius(12)
    ts = set(tiles)
    a, b = {}, {}
    ra = carve_rivers(999, a, tiles, ts, 12, RIVERS)
    rb = carve_rivers(999, b, tiles, ts, 12, RIVERS)
    assert ra == rb and a == b


def test_river_stream_is_independent_of_the_chamber_stream():
    """Named streams, so retuning chambers cannot silently move the rivers."""
    tiles = tiles_in_radius(12)
    ts = set(tiles)
    only_rivers = carve_rivers(4242, {}, tiles, ts, 12, RIVERS)
    with_chambers = _gen(["noise_fields", "macro_layout", "chambers", "rivers"],
                         chambers=CHAMBERS, rivers=RIVERS)
    solo = _gen(["noise_fields", "macro_layout", "rivers"], rivers=RIVERS)
    assert only_rivers  # sanity
    assert with_chambers.rivers == solo.rivers


# --- Chambers --------------------------------------------------------------

def test_a_hex_room_is_a_radial_clearing():
    tiles = set(tiles_in_radius(10))
    room = hex_room((0, 0), 3, tiles)
    assert all(hex_distance((0, 0), t) <= 3 for t in room)
    assert (3, 0) in room and (4, 0) not in room


def test_chambers_wall_off_everything_outside_the_rooms():
    layout = _gen(["noise_fields", "macro_layout", "chambers"], chambers=CHAMBERS)
    assert layout.chambers, "rooms should have been carved"
    walled = set(layout.tiles) - layout.chambers
    assert walled, "a chamber floor that walls nothing off is just an open floor"
    # Blocked is a subset of walled, not equal: the crossing carver reopens some.
    assert layout.blocked <= walled


def test_every_staircase_opens_into_a_room():
    """Anchoring rooms on the structural tiles first. A staircase in a corridor
    stub is technically reachable and reads as a generation accident."""
    layout = _gen(["noise_fields", "macro_layout", "chambers"], chambers=CHAMBERS)
    for tile in (layout.up_exit, layout.down_exit, (0, 0)):
        assert tile in layout.chambers


def test_chambers_are_deterministic():
    a = _gen(["noise_fields", "macro_layout", "chambers"], chambers=CHAMBERS)
    b = _gen(["noise_fields", "macro_layout", "chambers"], chambers=CHAMBERS)
    assert a.chambers == b.chambers and a.blocked == b.blocked


def test_chambers_keep_the_macro_biome_inside_the_rooms():
    """Rooms inherit the layout rather than replacing it, so a chamber floor
    still varies by biome instead of being one flat colour."""
    layout = _gen(["noise_fields", "macro_layout", "chambers"], chambers=CHAMBERS)
    inside = {layout.regions[t] for t in layout.chambers}
    assert "grass" in inside


# --- The division of labour this whole design rests on ---------------------

def test_a_river_may_bisect_the_floor_and_the_carver_resolves_it():
    """The point of rivers being a late topological overlay: they are allowed
    to cut the map in half, because the crossing carver runs after them and
    opens fords wherever the cut actually blocks a route."""
    layout = _gen(["noise_fields", "macro_layout", "rivers"], rivers=RIVERS)
    assert layout.rivers, "a river should have been drawn"
    assert layout.crossings, "and it should have needed at least one ford"
    assert layout.crossings <= layout.rivers, "fords are carved from river tiles"


def test_a_ford_is_open_ground_not_water():
    layout = _gen(["noise_fields", "macro_layout", "rivers"], rivers=RIVERS)
    for tile in layout.crossings:
        assert tile not in layout.blocked
        assert layout.regions[tile] == "grass"


def test_exits_stay_reachable_across_rivers_and_chambers_together():
    """Constraint 1, on the hardest combination: a chamber floor cut by
    rivers."""
    for floor in range(2, 25):
        layout = _gen(["noise_fields", "macro_layout", "chambers", "rivers"],
                      floor=floor, chambers=CHAMBERS, rivers=RIVERS)
        passable = terrain_predicate(set(layout.tiles), layout.blocked)
        start = layout.down_exit
        for exit_tile in (layout.up_exit, layout.down_exit):
            assert find_path(start, exit_tile, passable) is not None, (
                f"floor {floor}: {exit_tile} unreachable"
            )


def test_large_sections_of_the_map_are_not_left_isolated():
    """Constraint 2. Before islands were reconnected this archetype stranded
    40-80% of its own rooms -- every exit reachable, most of the dungeon not."""
    for floor in range(2, 20):
        layout = _gen(["noise_fields", "macro_layout", "chambers", "rivers"],
                      floor=floor, chambers=CHAMBERS, rivers=RIVERS)
        seen, open_tiles = _reachable(layout)
        assert len(seen) / len(open_tiles) > 0.85, (
            f"floor {floor}: only {100 * len(seen) / len(open_tiles):.0f}% reachable"
        )


def test_small_pockets_are_left_walled_off():
    """Reconnection is bounded. A floor with no isolated scenery at all would
    mean the threshold is not being honoured and barriers are decoration."""
    strays = 0
    for floor in range(2, 20):
        layout = _gen(["noise_fields", "macro_layout", "chambers", "rivers"],
                      floor=floor, chambers=CHAMBERS, rivers=RIVERS)
        components = open_components(set(layout.tiles), layout.blocked)
        strays += sum(1 for c in components[1:] if len(c) < RULESET["min_island_tiles"])
    assert strays > 0, "every pocket was reconnected; the threshold does nothing"


def test_reconnection_respects_the_configured_threshold():
    components_by_threshold = {}
    for threshold in (10, 400):
        ruleset = {**RULESET, "min_island_tiles": threshold}
        layout = generate_floor(
            "tower-a", 3, "seed-1", ruleset,
            archetypes=_archetype(["noise_fields", "macro_layout", "chambers", "rivers"],
                                  chambers=CHAMBERS, rivers=RIVERS),
            biomes=BIOMES,
        )
        seen, open_tiles = _reachable(layout)
        components_by_threshold[threshold] = len(seen) / len(open_tiles)
    # A lower threshold bridges more pockets, so more of the floor is reachable.
    assert components_by_threshold[10] >= components_by_threshold[400]


# --- Config contract -------------------------------------------------------

def _validate_pipeline(pipeline):
    """The ordering rule is a load-time contract, so it is checked against the
    validator rather than through generate_floor -- which takes an archetype
    dict directly and never sees ConfigStore."""
    ConfigStore._validate_pipeline("t", {"pipeline": pipeline, "rivers": RIVERS})


def test_a_feature_scheduled_before_the_layout_is_rejected():
    """It would be silently erased by the layout that follows -- a floor with
    no river and no error."""
    with pytest.raises(ConfigError, match="must come after 'macro_layout'"):
        _validate_pipeline(["noise_fields", "rivers", "macro_layout"])


def test_a_feature_without_a_macro_layout_is_rejected():
    with pytest.raises(ConfigError, match="must also be in 'pipeline'"):
        _validate_pipeline(["noise_fields", "rivers"])


def test_a_correctly_ordered_pipeline_is_accepted():
    """Pins that the two rejections above are about ordering specifically."""
    _validate_pipeline(["noise_fields", "macro_layout", "rivers"])


def test_shipping_archetypes_use_the_new_stages():
    """The archetypes exist and load -- the mechanical verification asked for."""
    import pathlib
    cfg = ConfigStore(pathlib.Path(__file__).resolve().parents[1] / "config")
    archetypes = cfg.floor_archetypes["archetypes"]
    assert "rivers" in archetypes["flooded_ruins"]["pipeline"]
    assert "chambers" in archetypes["flooded_ruins"]["pipeline"]
    assert archetypes["mountain_pass"]["layout"]["mode"] == "elevation"
    strata = {s["biome"] for s in archetypes["mountain_pass"]["layout"]["strata"]}
    assert {"mountain", "dense_forest"} <= strata


def test_the_client_can_derive_blocked_tiles_without_being_sent_them():
    """The snapshot ships `passable` per biome instead of a per-tile blocked
    array -- 52KB rather than 161KB on a chamber floor. That only works if the
    biome map plus the flag reproduces the server's blocked set exactly."""
    import pathlib
    from gep.server import build_floor_state, floor_snapshot
    cfg = ConfigStore(pathlib.Path(__file__).resolve().parents[1] / "config")
    state, _ = build_floor_state(11, cfg, lambda *a: None)
    snap = floor_snapshot(state, 0, 0.6, cfg.xp_table, cfg.biomes, cfg.items,
                          cfg.resources)
    assert "blocked" not in snap, "the per-tile array should not be on the wire"

    import base64
    legend = snap["biome_legend"]
    packed = base64.b64decode(snap["biome_map"])
    derived = {
        tile for tile, idx in zip(state.layout.tiles, packed)
        if not snap["biomes"][legend[idx]]["passable"]
    }
    assert derived == state.layout.blocked


def test_impassable_biomes_declare_no_spawns():
    import pathlib
    cfg = ConfigStore(pathlib.Path(__file__).resolve().parents[1] / "config")
    for bid in ("water", "mountain", "dense_forest"):
        assert cfg.biomes[bid]["passable"] is False
        assert not cfg.biomes[bid]["monster_weights"]
        assert not cfg.biomes[bid]["resource_weights"]
