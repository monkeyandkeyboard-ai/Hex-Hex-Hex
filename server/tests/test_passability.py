"""Impassable terrain (gep/passability.py) and the guarantees around it.

Nothing in shipping config is impassable yet -- this pass builds the machinery,
and the water/mountain/river features land on top of it. So these tests
construct their own biome tables rather than reading ConfigStore: a test that
asserted "no tile is blocked" against live config would pass today, pass after
someone adds a lake, and prove nothing on either day.
"""
import pytest

from gep.constraints import GenerationError, validate_connectivity
from gep.entities import Monster, Player, Skills
from gep.floor_state import FloorState
from gep.floorgen import FloorLayout
from gep.hexgrid import tiles_in_radius
from gep.passability import blocked_tiles, terrain_predicate
from gep.roads import build_roads

WALKABLE = {"passable": True}
SOLID = {"passable": False}
BIOMES = {"grass": WALKABLE, "stone": SOLID}


def _layout(tiles, regions=None, blocked=None, up=(3, 0), down=(-3, 0)):
    return FloorLayout(
        tower_id="t", floor_number=2, radius=3, tiles=list(tiles),
        up_exit=up, down_exit=down,
        regions=regions or {}, blocked=set(blocked or ()),
    )


# --- deriving blocked tiles from biomes -----------------------------------

def test_blocked_tiles_come_from_the_biome_not_a_separate_overlay():
    regions = {(0, 0): "grass", (1, 0): "stone", (2, 0): "stone"}
    assert blocked_tiles(regions, BIOMES) == {(1, 0), (2, 0)}


def test_no_impassable_biome_means_nothing_is_blocked():
    regions = {(0, 0): "grass", (1, 0): "grass"}
    assert blocked_tiles(regions, BIOMES) == set()


def test_absent_biome_table_leaves_every_tile_walkable():
    """The legacy generation path assigns no regions and passes no biomes.
    It must keep producing fully-walkable floors."""
    assert blocked_tiles({}, None) == set()
    assert blocked_tiles({(0, 0): "grass"}, None) == set()


def test_a_tile_with_no_region_is_walkable():
    """Absence of terrain data means terrain imposes no restriction, not that
    the tile is solid rock -- the opposite default would make every partially
    generated floor impassable."""
    assert blocked_tiles({(0, 0): "stone"}, BIOMES) == {(0, 0)}
    assert (5, 5) not in blocked_tiles({(0, 0): "stone"}, BIOMES)


# --- the shared predicate --------------------------------------------------

def test_terrain_predicate_rejects_off_floor_and_blocked_alike():
    tiles = set(tiles_in_radius(2))
    passable = terrain_predicate(tiles, {(1, 0)})
    assert passable((0, 0))
    assert not passable((1, 0))          # blocked
    assert not passable((99, 99))        # off the floor


# --- connectivity is no longer a tautology ---------------------------------

def _wall_across(radius):
    """Every tile on the q = 0 column, which cuts a hex disc clean in two."""
    return {t for t in tiles_in_radius(radius) if t[0] == 0}


def test_a_wall_between_spawn_and_exit_fails_generation():
    tiles = set(tiles_in_radius(3))
    blocked = _wall_across(3)
    with pytest.raises(GenerationError, match="not reachable"):
        validate_connectivity(
            tiles, (-3, 0), [(3, 0)], terrain_predicate(tiles, blocked)
        )


def test_the_same_wall_passes_without_the_predicate():
    """Pins that the test above is detecting the barrier and not some unrelated
    breakage: the identical geometry validates fine when terrain is cosmetic,
    which is exactly the behaviour that shipped before this change."""
    tiles = set(tiles_in_radius(3))
    validate_connectivity(tiles, (-3, 0), [(3, 0)])


def test_an_exit_buried_in_terrain_is_reported_as_such():
    """find_path treats the goal as reachable even when it isn't passable, so
    without an explicit check a buried exit would validate silently."""
    tiles = set(tiles_in_radius(3))
    with pytest.raises(GenerationError, match="on impassable terrain"):
        validate_connectivity(
            tiles, (0, 0), [(3, 0)], terrain_predicate(tiles, {(3, 0)})
        )


def test_a_buried_spawn_is_reported_as_such():
    tiles = set(tiles_in_radius(3))
    with pytest.raises(GenerationError, match="spawn .* impassable"):
        validate_connectivity(
            tiles, (0, 0), [(3, 0)], terrain_predicate(tiles, {(0, 0)})
        )


def test_a_gap_in_the_wall_is_enough():
    tiles = set(tiles_in_radius(3))
    blocked = _wall_across(3) - {(0, 0)}
    validate_connectivity(
        tiles, (-3, 0), [(3, 0)], terrain_predicate(tiles, blocked)
    )


# --- roads route around barriers -------------------------------------------

def test_roads_route_around_a_barrier_rather_than_through_it():
    tiles = set(tiles_in_radius(3))
    blocked = _wall_across(3) - {(0, -3)}     # single gap at the top
    roads = build_roads(tiles, (3, 0), (-3, 0), terrain_predicate(tiles, blocked))

    assert not (roads & blocked), "road crosses impassable terrain"
    assert (0, -3) in roads, "road should thread the only gap"


def test_roads_and_connectivity_agree_by_construction():
    """The failure this shape prevents: carving a road under a lax rule and
    validating reachability under a strict one. The road would cross the lake,
    the floor would validate, and the player would follow a road into water."""
    tiles = set(tiles_in_radius(3))
    blocked = _wall_across(3) - {(0, -3)}
    passable = terrain_predicate(tiles, blocked)

    roads = build_roads(tiles, (3, 0), (-3, 0), passable)
    validate_connectivity(tiles, (-3, 0), [(3, 0)], passable)
    assert all(passable(t) for t in roads)


# --- enforcement at movement time ------------------------------------------

def _monster(tile, alive=True):
    return Monster(
        id="m1", template_id="cave_rat", floor_number=2, tile=tile,
        hp=5, max_hp=5, stats={}, damage_min=1, damage_max=2, speed_ticks=4,
        alive=alive,
    )


def _player():
    return Player(
        id="p1", name="p1", tower_id="t", floor_number=2, tile=(0, 0),
        hp=10, max_hp=10, mana=1, max_mana=1, weapon_id="unarmed", skills=Skills(),
    )


def test_floor_state_refuses_to_walk_onto_blocked_terrain():
    floor = FloorState.from_layout(_layout(tiles_in_radius(3), blocked={(1, 0)}))
    assert floor.is_passable((0, 0))
    assert not floor.is_passable((1, 0))


def test_terrain_blocks_monsters_on_the_same_gate_as_players():
    """Both movement.py and monster_ai.py go through is_passable, so terrain
    that stopped one and not the other would let a goblin swim."""
    floor = FloorState.from_layout(_layout(tiles_in_radius(3), blocked={(1, 0)}))
    floor.players["p1"] = _player()
    floor.monsters["m1"] = _monster((2, 0))
    # One gate, one answer -- there is no player-only or monster-only variant.
    assert not floor.is_passable((1, 0))


def test_entity_blocking_still_works_alongside_terrain():
    """Terrain is fixed for the life of the floor; entities are not. The two
    must both apply, and neither may shadow the other."""
    floor = FloorState.from_layout(_layout(tiles_in_radius(3), blocked={(1, 0)}))
    floor.monsters["m1"] = _monster((2, 0))
    assert not floor.is_passable((1, 0))   # terrain
    assert not floor.is_passable((2, 0))   # entity
    assert floor.is_passable((0, 1))       # neither


def test_a_dead_monster_unblocks_but_terrain_never_does():
    floor = FloorState.from_layout(_layout(tiles_in_radius(3), blocked={(1, 0)}))
    floor.monsters["m1"] = _monster((2, 0), alive=False)
    assert floor.is_passable((2, 0)), "a corpse should not block"
    assert not floor.is_passable((1, 0)), "terrain does not die"


# --- the pipeline actually wires it up -------------------------------------
# The tests above all drive components directly. That left the wiring itself
# uncovered: stage_roads could stop passing the predicate and every one of them
# would still pass. These go through generate_floor.

def _archetype(strata_biome, below=0.30):
    """`below` is the share of the elevation range the barrier biome occupies.
    Kept low by default so these read as lakes in a landscape rather than a
    landscape with some land in it -- a template that blocks half the map
    disconnects its own exits, which is a config bug and is tested as one."""
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
                        {"below": below, "biome": strata_biome},
                        {"below": 1.01, "biome": "grass"},
                    ],
                },
                "elevation": {"octaves": 3, "scale": 0.08, "flatten": []},
                "roughness": {"octaves": 3, "scale": 0.2},
            }
        },
    }


GEN_BIOMES = {"grass": WALKABLE, "stone": SOLID}
RULESET = {"radius": 12, "exit_separation": {"min_moves": 6, "max_diameter_pct": 0.9}}


def _generate(strata_biome, floor=2, below=0.30):
    from gep.floorgen import generate_floor
    return generate_floor(
        "tower-a", floor, "seed-1", RULESET,
        archetypes=_archetype(strata_biome, below), biomes=GEN_BIOMES,
    )


def test_generate_floor_populates_blocked_from_the_biome_table():
    layout = _generate("stone")
    assert layout.blocked, "an impassable stratum should block tiles"
    assert all(layout.regions[t] == "stone" for t in layout.blocked)


def test_an_all_walkable_template_blocks_nothing():
    assert _generate("grass").blocked == set()


def test_the_generated_road_never_crosses_generated_terrain():
    """The wiring test. If stage_roads stops passing the predicate this fails,
    where every component-level test above would still pass."""
    layout = _generate("stone")
    assert layout.roads, "floor should have a road spine"
    assert not (layout.roads & layout.blocked)


def test_generation_is_deterministic_including_barriers():
    a, b = _generate("stone"), _generate("stone")
    assert a.blocked == b.blocked
    assert a.roads == b.roads


def test_terrain_can_never_bury_a_staircase():
    """Exits are chosen before terrain exists, so without the structural clear
    a lake eventually lands on one. Swept across many floors because a single
    seed proves only that this seed was lucky."""
    for floor in range(2, 40):
        layout = _generate("stone", floor=floor, below=0.75)
        assert layout.up_exit not in layout.blocked, f"floor {floor}: up exit buried"
        assert layout.down_exit not in layout.blocked, f"floor {floor}: down exit buried"
        assert (0, 0) not in layout.blocked, f"floor {floor}: entrance buried"


def test_a_cleared_staircase_reads_as_walkable_terrain_too():
    """Exempting the tile from `blocked` without rewriting its region would
    render a staircase submerged in a lake the player then walks across."""
    layout = _generate("stone", below=0.75)
    assert layout.regions[layout.up_exit] == "grass"
    assert layout.regions[(0, 0)] == "grass"


def test_clearing_leaves_an_already_walkable_structural_tile_alone():
    """Need-driven. Clearing unconditionally made the tower entrance stop being
    town biome on a town floor merely because a rule about lakes exists."""
    layout = _generate("grass")
    assert layout.regions[(0, 0)] == "grass"
    assert layout.regions[layout.up_exit] == "grass"


# --- crossings are guaranteed, not hoped for -------------------------------

def test_every_exit_is_reachable_however_much_barrier_there_is():
    """Constraint 1, swept. Barrier coverage from a trace to nearly total; the
    exits stay reachable at every level because crossings are carved, not
    checked for."""
    for below in (0.15, 0.30, 0.50, 0.75, 0.90):
        for floor in range(2, 12):
            layout = _generate("stone", floor=floor, below=below)
            passable = terrain_predicate(set(layout.tiles), layout.blocked)
            from gep.pathfinding import find_path
            for exit_tile in (layout.up_exit, layout.down_exit):
                assert find_path(layout.down_exit, exit_tile, passable) is not None, (
                    f"below={below} floor={floor}: {exit_tile} unreachable"
                )


def test_a_carved_crossing_is_recorded_so_it_can_be_rendered():
    """A crossing has to be identifiable as a crossing -- otherwise a bridge is
    indistinguishable from ordinary ground the barrier happened to miss."""
    layout = _generate("stone", below=0.75)
    assert layout.crossings, "a heavily blocked floor should need crossings"
    assert not (layout.crossings & layout.blocked), "a crossing is open by definition"
    assert all(layout.regions[t] == "grass" for t in layout.crossings)


def test_an_unblocked_floor_carves_nothing():
    """Carving must be driven by need. A floor with no barriers that still
    reported crossings would mean the mechanism runs unconditionally."""
    assert _generate("grass").crossings == set()


def test_crossings_are_minimal_rather_than_a_cleared_lane():
    """Only tiles on the shortest route are opened, so the barrier survives
    everywhere the player does not need to cross it."""
    layout = _generate("stone", below=0.75)
    assert layout.blocked, "carving should not have flattened the whole barrier"
    assert len(layout.crossings) < len(layout.blocked)


def test_crossings_are_deterministic():
    """No PRNG is consumed, so this must hold exactly."""
    a, b = _generate("stone", below=0.75), _generate("stone", below=0.75)
    assert a.crossings == b.crossings
    assert a.blocked == b.blocked


def test_crossings_ship_in_the_payload():
    layout = _generate("stone", below=0.75)
    assert sorted(layout.to_dict()["crossings"]) == sorted(
        [list(t) for t in layout.crossings]
    )


def test_spawns_never_land_inside_a_barrier():
    from gep.spawner import spawn_floor
    layout = _generate("stone")
    # The spawner reads the full biome shape, not just passability. Only the
    # walkable biome carries weights -- config rejects an impassable biome that
    # declares any, since nothing could reach those tiles to spawn on them.
    spawn_biomes = {
        "grass": {**WALKABLE, "resource_spawn_chance": 0.2, "monster_weight": 1.0,
                  "resource_weights": [["iron_ore", 1]],
                  "monster_weights": [["cave_rat", 1]]},
        "stone": {**SOLID, "resource_spawn_chance": 0.0, "monster_weight": 0.0,
                  "resource_weights": [], "monster_weights": []},
    }
    plan = spawn_floor(
        layout, spawn_biomes,
        {"archetype_monster_counts": {"test": 20}, "road_danger_factor": 0.1},
        "spawn-seed",
    )
    placed = set(plan.resource_nodes) | {tuple(s["tile"]) for s in plan.monster_spawns}
    assert placed, "the fixture should actually spawn something"
    assert not (placed & layout.blocked)


# --- the wire payload ------------------------------------------------------

def test_blocked_tiles_ship_in_the_payload():
    layout = _layout(tiles_in_radius(3), blocked={(1, 0), (0, 1)})
    assert sorted(layout.to_dict()["blocked"]) == [[0, 1], [1, 0]]


def test_a_floor_with_no_barriers_ships_an_empty_list():
    assert _layout(tiles_in_radius(3)).to_dict()["blocked"] == []
