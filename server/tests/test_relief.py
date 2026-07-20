"""Barrier relief: giving impassable terrain physical height.

The point of these tests is that height and impassability are separate
properties that have to be made to agree. The elevation noise field decides
what the land looks like; the biome table decides what stops the player. On a
chamber floor those two are fully decoupled -- the wall biome is stamped on by
the carver, so a wall inherits whatever height the noise gave it -- and without
relief a dungeon wall can sit lower than the room next to it.
"""
import pytest

from gep.biome_layout import apply_barrier_relief
from gep.config_loader import ConfigError, ConfigStore

WALKABLE = {"passable": True}
SOLID = {"passable": False}
TALL = {"passable": False, "height_boost": 0.3}
BIOMES = {"grass": WALKABLE, "cliff": TALL}


# --- the relief function itself -------------------------------------------

def test_barrier_tiles_are_raised():
    elevation = {(0, 0): 0.5, (1, 0): 0.5}
    regions = {(0, 0): "grass", (1, 0): "cliff"}
    out = apply_barrier_relief(elevation, regions, BIOMES)
    assert out[(1, 0)] == pytest.approx(0.8)


def test_walkable_ground_is_left_exactly_alone():
    elevation = {(0, 0): 0.5}
    out = apply_barrier_relief(elevation, {(0, 0): "grass"}, BIOMES)
    assert out[(0, 0)] == 0.5


def test_relief_adds_to_the_noise_instead_of_replacing_it():
    """A mountain range has to keep the shape the noise gave it. If relief
    assigned a height rather than adding one, every barrier tile would land on
    the same value and the range would render as a flat-topped plateau."""
    elevation = {(0, 0): 0.2, (1, 0): 0.6}
    regions = {(0, 0): "cliff", (1, 0): "cliff"}
    out = apply_barrier_relief(elevation, regions, BIOMES)
    assert out[(0, 0)] != out[(1, 0)]
    assert out[(1, 0)] - out[(0, 0)] == pytest.approx(0.4)


def test_relief_is_clamped_to_the_unit_range():
    """Elevation ships as a byte quantised against [0, 1], so anything above
    1.0 is not a taller mountain, it is a wrapped or clipped one."""
    elevation = {(0, 0): 0.9}
    out = apply_barrier_relief(elevation, {(0, 0): "cliff"}, BIOMES)
    assert out[(0, 0)] == 1.0


def test_a_barrier_biome_without_a_boost_stays_at_terrain_height():
    """height_boost is optional. Water is impassable but is not a peak, and
    raising it would make rivers run along ridges."""
    elevation = {(0, 0): 0.4}
    out = apply_barrier_relief(elevation, {(0, 0): "lake"},
                               {"lake": SOLID})
    assert out[(0, 0)] == 0.4


def test_no_biome_table_is_a_no_op():
    elevation = {(0, 0): 0.4}
    assert apply_barrier_relief(elevation, {(0, 0): "cliff"}, None) == elevation


def test_relief_consumes_no_randomness():
    """A pure function of regions plus the biome table. Running it twice on
    equal input must agree, or it could shift a seed's terrain."""
    a = apply_barrier_relief({(0, 0): 0.5}, {(0, 0): "cliff"}, BIOMES)
    b = apply_barrier_relief({(0, 0): 0.5}, {(0, 0): "cliff"}, BIOMES)
    assert a == b


# --- wiring: relief runs at the one point where it is correct -------------

def _archetype():
    return {
        "default_archetype": "test",
        "overrides": [],
        "archetypes": {
            "test": {
                "safe": False,
                "fallback_biome": "grass",
                "pipeline": ["noise_fields", "macro_layout"],
                "layout": {
                    "mode": "elevation",
                    "strata": [
                        {"below": 0.35, "biome": "cliff"},
                        {"below": 1.01, "biome": "grass"},
                    ],
                },
                "elevation": {"octaves": 3, "scale": 0.08, "flatten": []},
                "roughness": {"octaves": 3, "scale": 0.2},
            }
        },
    }


RULESET = {
    "radius": 12,
    "exit_separation": {"min_moves": 6, "max_diameter_pct": 0.9},
    "min_island_tiles": 20,
}


def _generate(biomes):
    from gep.floorgen import generate_floor
    return generate_floor(
        "tower-a", 2, "seed-1", RULESET, archetypes=_archetype(), biomes=biomes,
    )


def test_every_generated_barrier_tile_gains_exactly_its_boost():
    """The guarantee relief actually makes, stated per tile rather than as an
    average.

    It deliberately does NOT assert that barriers end up higher than open
    ground overall, because that is not something relief can promise: this
    template puts the barrier biome in the *lowest* elevation stratum, so a
    0.3 boost lifts walls out of the valley but not above the hills. Whether a
    barrier out-tops its surroundings depends on the boost against the terrain
    it was stamped into -- a tuning relationship, and one worth knowing is a
    tuning relationship rather than a guarantee.
    """
    flat, tall = _generate({"grass": WALKABLE, "cliff": SOLID}), _generate(BIOMES)
    raised = tall.blocked - tall.crossings
    assert raised
    for tile in raised:
        expected = min(1.0, flat.elevation[tile] + 0.3)
        assert tall.elevation[tile] == pytest.approx(expected)


def test_a_barrier_in_the_high_stratum_reads_as_high_ground():
    """The natural-terrain case, matching how mountain_pass is configured:
    when the barrier biome already occupies the top of the elevation range,
    relief pushes it clear of everything around it."""
    archetype = _archetype()
    archetype["archetypes"]["test"]["layout"]["strata"] = [
        {"below": 0.65, "biome": "grass"},
        {"below": 1.01, "biome": "cliff"},
    ]

    from gep.floorgen import generate_floor
    layout = generate_floor(
        "tower-a", 2, "seed-1", RULESET, archetypes=archetype, biomes=BIOMES,
    )
    assert layout.blocked
    barrier_mean = sum(layout.elevation[t] for t in layout.blocked) / len(layout.blocked)
    open_tiles = [t for t in layout.tiles if t not in layout.blocked]
    open_mean = sum(layout.elevation[t] for t in open_tiles) / len(open_tiles)
    assert barrier_mean > open_mean


def test_a_carved_ford_is_not_left_standing_at_cliff_height():
    """The ordering test, and the reason relief runs after crossings rather
    than with the rest of the elevation work.

    A crossing is a barrier tile that was opened, so at the moment relief runs
    it is walkable ground and must be at walkable height. Move relief earlier
    in run_pipeline and this fails: the fords keep the boost they were given
    while they were still wall, and every bridge on the floor renders as a
    column standing as tall as the cliff it was cut through.
    """
    flat, tall = _generate({"grass": WALKABLE, "cliff": SOLID}), _generate(BIOMES)
    assert tall.crossings, "the template should need at least one crossing"
    for tile in tall.crossings:
        assert tall.elevation[tile] == pytest.approx(flat.elevation[tile]), (
            f"crossing {tile} was raised; relief ran before the ford was cut"
        )


def test_only_barrier_tiles_differ_between_a_relieved_and_flat_floor():
    """Relief must not perturb anything else -- same seed, same everything,
    with the boost as the only difference."""
    flat, tall = _generate({"grass": WALKABLE, "cliff": SOLID}), _generate(BIOMES)
    assert flat.blocked == tall.blocked, "relief must not change what blocks"
    assert flat.roads == tall.roads, "relief must not reroute the roads"
    changed = {t for t in tall.tiles if tall.elevation[t] != flat.elevation[t]}
    assert changed == tall.blocked


# --- config contract -------------------------------------------------------

def _store(biomes):
    store = ConfigStore.__new__(ConfigStore)
    store.biomes = biomes
    store.resources = {}
    store.monsters = {}
    return store


def _biome(**overrides):
    base = {
        "id": "b", "display_name": "B", "color": "#000",
        "resource_spawn_chance": 0.0, "resource_weights": [],
        "monster_weight": 0.0, "monster_weights": [], "passable": False,
    }
    base.update(overrides)
    return base


def test_a_valid_height_boost_is_accepted():
    """Positive control: without this the rejection tests below would pass
    even if validation rejected everything."""
    _store({"b": _biome(height_boost=0.4)})._validate_biomes()


def test_relief_on_walkable_ground_is_rejected():
    store = _store({"b": _biome(passable=True, height_boost=0.4)})
    with pytest.raises(ConfigError, match="height_boost"):
        store._validate_biomes()


def test_a_height_boost_outside_the_unit_range_is_rejected():
    store = _store({"b": _biome(height_boost=1.6)})
    with pytest.raises(ConfigError, match="between 0 and 1"):
        store._validate_biomes()


def test_a_non_numeric_height_boost_is_rejected():
    store = _store({"b": _biome(height_boost="tall")})
    with pytest.raises(ConfigError, match="must be a number"):
        store._validate_biomes()
