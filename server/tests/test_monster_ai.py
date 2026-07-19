import pathlib
import random

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.hexgrid import facing_from_delta
from gep.pathfinding import hex_neighbors
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

# Always wander, every tick, so behaviour is deterministic to assert on.
ALWAYS_MOVE = {"cave_rat": {"movement": {"wander_interval_ticks": 1, "wander_chance": 1.0}}}
NEVER_MOVE = {"cave_rat": {"movement": {"wander_interval_ticks": 1, "wander_chance": 0.0}}}


def make_floor():
    layout = generate_floor("tower-a", 5, "test", RULESET)
    return FloorState.from_layout(layout)


def add_monster(floor, tile=(0, 0), mid="m1"):
    cfg = ConfigStore(CONFIG_DIR)
    monster = roll_monster(mid, cfg.monsters["cave_rat"], cfg.stat_scaling)
    monster.tile = tile
    monster.floor_number = 5
    floor.monsters[mid] = monster
    return monster


def run(engine, ticks):
    events = []
    for _ in range(ticks):
        events.extend(engine.step([]).events)
    return events


def test_monster_wanders_to_adjacent_tile_and_updates_facing():
    floor = make_floor()
    monster = add_monster(floor)
    engine = TickEngine()
    monster_ai.register(engine, floor, ALWAYS_MOVE, rng=random.Random(1))

    start = monster.tile
    moves = [e for e in run(engine, 6) if e["type"] == "monster_moved"]

    assert moves, "monster never moved"
    assert monster.tile != start
    # Every emitted move is a single step to a neighbour, and the reported
    # facing matches the direction actually travelled.
    prev = start
    for ev in moves:
        tile = tuple(ev["tile"])
        assert tile in hex_neighbors(*prev)
        assert ev["facing"] == facing_from_delta(prev, tile)
        prev = tile
    assert monster.facing == moves[-1]["facing"]


def test_wander_chance_zero_keeps_monster_still():
    floor = make_floor()
    monster = add_monster(floor)
    engine = TickEngine()
    monster_ai.register(engine, floor, NEVER_MOVE, rng=random.Random(2))

    start = monster.tile
    moves = [e for e in run(engine, 10) if e["type"] == "monster_moved"]

    assert moves == []
    assert monster.tile == start


def test_monsters_never_share_a_tile_with_a_player_or_each_other():
    floor = make_floor()
    for i, tile in enumerate([(0, 0), (1, 0), (0, 1), (2, 0), (-1, 1)]):
        add_monster(floor, tile=tile, mid=f"m{i}")
    player_tile = (1, 1)
    floor.players["p1"] = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=player_tile,
        hp=10, max_hp=10, mana=0, max_mana=0, weapon_id="fists", skills=Skills(),
    )

    engine = TickEngine()
    monster_ai.register(engine, floor, ALWAYS_MOVE, rng=random.Random(3))

    # Check the invariant after every tick, not just at the end -- a monster
    # could otherwise step onto the player and step off again unnoticed.
    for _ in range(40):
        engine.step([])
        tiles = [m.tile for m in floor.monsters.values()]
        assert player_tile not in tiles, "monster walked onto the player"
        assert len(tiles) == len(set(tiles)), "two monsters occupied one tile"
        assert all(floor.is_valid_tile(t) for t in tiles), "monster left the floor"


def test_monster_boxed_in_stays_put():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    # Blockers are registered before the AI, but excluded from its timers by
    # registering first and adding them after -- so they never move away.
    engine = TickEngine()
    monster_ai.register(engine, floor, ALWAYS_MOVE, rng=random.Random(5))
    for i, tile in enumerate(hex_neighbors(0, 0)):
        add_monster(floor, tile=tile, mid=f"blocker{i}")

    run(engine, 8)

    assert monster.tile == (0, 0)


def test_dead_monster_holds_still_but_resumes_after_respawn():
    floor = make_floor()
    monster = add_monster(floor)
    engine = TickEngine()
    monster_ai.register(engine, floor, ALWAYS_MOVE, rng=random.Random(4))

    monster.alive = False
    start = monster.tile
    assert [e for e in run(engine, 5) if e["type"] == "monster_moved"] == []
    assert monster.tile == start

    # The timer must survive death, or a respawned monster would be frozen.
    monster.alive = True
    assert [e for e in run(engine, 5) if e["type"] == "monster_moved"]
