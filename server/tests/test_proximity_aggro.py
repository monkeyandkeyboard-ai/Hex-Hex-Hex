"""Proximity aggro and the hex line-of-sight it is gated on.

Complements test_aggro.py, which covers threat driven by damage. Here the
monster acquires a target it was never hit by -- a player who simply walked
into view -- and the sight check that stops it seeing through walls.
"""
import pathlib
import random

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.hexgrid import hex_distance, hex_line
from gep.systems import monster_ai
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}

# Step every tick, and notice players within 4 tiles.
AGGRESSIVE = {"cave_rat": {"movement": {
    "wander_interval_ticks": 1, "wander_chance": 1.0,
    "pursue_interval_ticks": 1, "aggro_radius": 4,
}}}
# Same, but blind: aggro_radius defaults to 0 (passive) when omitted.
PASSIVE = {"cave_rat": {"movement": {
    "wander_interval_ticks": 1, "wander_chance": 1.0, "pursue_interval_ticks": 1,
}}}


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def add_monster(floor, tile=(0, 0), mid="m1"):
    cfg = ConfigStore(CONFIG_DIR)
    monster = roll_monster(mid, cfg.monsters["cave_rat"], cfg.stat_scaling)
    monster.tile = tile
    monster.floor_number = 5
    floor.monsters[mid] = monster
    return monster


def add_player(floor, tile=(0, 3), pid="p1"):
    player = Player(
        id=pid, name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=100, max_hp=100, mana=0, max_mana=0, weapon_id="unarmed", skills=Skills(),
    )
    floor.players[pid] = player
    return player


def run(engine, ticks):
    for _ in range(ticks):
        engine.step([])


# --- hex_line / sight -------------------------------------------------------

def test_hex_line_endpoints_and_length():
    line = hex_line((0, 0), (0, 3))
    assert line[0] == (0, 0) and line[-1] == (0, 3)
    # A straight line of hex-distance d passes through d+1 tiles.
    assert len(line) == hex_distance((0, 0), (0, 3)) + 1


def test_hex_line_is_symmetric():
    a, b = (0, 0), (2, -3)
    assert hex_line(a, b) == list(reversed(hex_line(b, a)))


def test_sight_is_clear_over_open_terrain():
    floor = make_floor()
    assert floor.sight_clear((0, 0), (0, 4))


def test_sight_is_blocked_by_a_wall_between():
    floor = make_floor()
    # Wall the tile the straight line passes through.
    mid = hex_line((0, 0), (0, 4))[2]
    floor.layout.blocked.add(mid)
    assert not floor.sight_clear((0, 0), (0, 4))


def test_sight_ignores_walls_on_the_endpoints_themselves():
    """A cliff *under* the target does not stop you seeing the target; only
    terrain strictly between the two blocks the view."""
    floor = make_floor()
    floor.layout.blocked.add((0, 4))   # the target tile
    assert floor.sight_clear((0, 0), (0, 4))


# --- proximity acquisition --------------------------------------------------

def test_passive_monster_never_acquires_unprompted():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    add_player(floor, tile=(0, 2))          # well within any sane radius
    engine = TickEngine()
    monster_ai.register(engine, floor, PASSIVE, rng=random.Random(1))

    run(engine, 5)
    assert monster.threat_target is None, "aggro_radius 0 must stay passive"


def test_monster_acquires_a_visible_player_in_range():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    player = add_player(floor, tile=(0, 3))
    engine = TickEngine()
    monster_ai.register(engine, floor, AGGRESSIVE, rng=random.Random(2))

    run(engine, 2)
    assert monster.threat_target == "p1"
    # And having noticed, it closes in.
    run(engine, 12)
    assert hex_distance(monster.tile, player.tile) == 1


def test_monster_does_not_acquire_a_player_out_of_range():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    add_player(floor, tile=(0, 7))          # radius is 4
    engine = TickEngine()
    monster_ai.register(engine, floor, AGGRESSIVE, rng=random.Random(3))

    run(engine, 5)
    assert monster.threat_target is None


def test_wall_between_blocks_acquisition():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    add_player(floor, tile=(0, 3))
    # Drop a wall on the sightline so the player is in range but not visible.
    for t in hex_line((0, 0), (0, 3))[1:-1]:
        floor.layout.blocked.add(t)
    engine = TickEngine()
    monster_ai.register(engine, floor, AGGRESSIVE, rng=random.Random(4))

    run(engine, 5)
    assert monster.threat_target is None, "saw the player through a wall"


def test_proximity_aggro_is_sticky_when_the_target_flees_out_of_range():
    """Owner's choice: no leash. Once latched, the monster keeps the target
    even after it runs well beyond the aggro radius -- only death or leaving
    the floor drops it (covered in test_aggro.py)."""
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    player = add_player(floor, tile=(0, 3))
    engine = TickEngine()
    monster_ai.register(engine, floor, AGGRESSIVE, rng=random.Random(5))

    run(engine, 2)
    assert monster.threat_target == "p1"

    player.tile = (0, 8)                      # far outside radius 4
    run(engine, 3)
    assert monster.threat_target == "p1", "aggro should be sticky, not leashed"
