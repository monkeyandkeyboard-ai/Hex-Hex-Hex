import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.systems import movement
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}


def make_floor():
    layout = generate_floor("tower-a", 5, "test", RULESET)
    return FloorState.from_layout(layout)


def make_player(pid="p1", tile=(0, 0)):
    return Player(
        id=pid, name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=1000, max_hp=1000, mana=50, max_mana=50, weapon_id="fists",
        skills=Skills(),
    )


def setup_engine(floor):
    engine = TickEngine()
    movement.register(engine, floor)
    return engine


def test_move_to_adjacent_tile_advances_on_intent_tick():
    """Clicking is responsive: the intent tick itself advances the player
    (intents drain before actions, and movement schedules at delay=0)."""
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 1, "target_r": 0}])
    started = [e for e in result.events if e["type"] == "move_started"]
    pos_updates = [e for e in result.events if e["type"] == "position_update"]
    assert len(started) == 1
    assert len(pos_updates) == 1
    assert pos_updates[0]["tile"] == [1, 0]
    assert player.tile == (1, 0)


def test_move_at_speed_2_covers_2_tiles_per_tick():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    player.move_speed = 2
    floor.players["p1"] = player
    engine = setup_engine(floor)

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 3, "target_r": 0}])
    # Intent tick advances by speed (2), leaving 1 tile.
    assert len([e for e in result.events if e["type"] == "position_update"]) == 2

    result2 = engine.step([])
    assert len([e for e in result2.events if e["type"] == "position_update"]) == 1
    assert player.tile == (3, 0)


def test_multi_tile_path_arrives_at_destination():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    target = (4, -2)
    engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": target[0], "target_r": target[1]}])

    for _ in range(10):  # enough ticks to complete the path
        engine.step([])
        if player.tile == target:
            break

    assert player.tile == target


def test_move_to_same_tile_emits_no_events():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 0, "target_r": 0}])
    assert not any(e["type"] in ("move_started", "position_update") for e in result.events)


def test_move_off_floor_returns_error():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 999, "target_r": 999}])
    assert any(e["type"] == "error" for e in result.events)
    assert player.tile == (0, 0)


def test_new_move_intent_supersedes_previously_queued_step():
    """Clicking a new destination mid-walk redirects on that very tick.
    The stale move-step already queued from the old path must drop, and
    the fresh path takes its place -- no dead tick between them.
    """
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    # Walk east: (0,0) -> (0,1) on the intent tick, (0,2) on the next.
    engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 0, "target_r": 4}])
    engine.step([])
    assert player.tile == (0, 2)

    # Redirect west. Two move-steps are due this tick: the stale one from
    # the old path (queued last tick), plus the fresh delay=0 one from the
    # new intent. Seq check drops the old; the new advances toward (-3, 2).
    result = engine.step([
        {"intent_type": "move-to-tile", "player_id": "p1", "target_q": -3, "target_r": 2}
    ])
    assert any(e["type"] == "move_started" for e in result.events)
    positions = [tuple(e["tile"]) for e in result.events if e["type"] == "position_update"]
    assert len(positions) == 1
    assert positions[0] == (-1, 2)
    assert player.tile == (-1, 2)


def test_each_intermediate_tile_emitted_as_separate_position_update():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    player.move_speed = 1
    floor.players["p1"] = player
    engine = setup_engine(floor)

    all_positions = []
    r = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 0, "target_r": 3}])
    all_positions.extend(e["tile"] for e in r.events if e["type"] == "position_update")
    for _ in range(5):
        r = engine.step([])
        all_positions.extend(e["tile"] for e in r.events if e["type"] == "position_update")

    assert len(all_positions) == 3
    assert all_positions[-1] == [0, 3]
