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


def test_move_to_adjacent_tile_takes_one_tick():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    engine = setup_engine(floor)

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 1, "target_r": 0}])
    started = [e for e in result.events if e["type"] == "move_started"]
    assert len(started) == 1
    assert player.tile == (0, 0)  # not yet moved

    result2 = engine.step([])
    pos_updates = [e for e in result2.events if e["type"] == "position_update"]
    assert len(pos_updates) == 1
    assert pos_updates[0]["tile"] == [1, 0]
    assert player.tile == (1, 0)


def test_move_at_speed_2_covers_2_tiles_per_tick():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    player.move_speed = 2
    floor.players["p1"] = player
    engine = setup_engine(floor)

    engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 3, "target_r": 0}])

    result = engine.step([])  # first move step: 2 tiles
    pos_updates = [e for e in result.events if e["type"] == "position_update"]
    assert len(pos_updates) == 2

    result2 = engine.step([])  # second move step: 1 remaining tile
    pos_updates2 = [e for e in result2.events if e["type"] == "position_update"]
    assert len(pos_updates2) == 1
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


def test_each_intermediate_tile_emitted_as_separate_position_update():
    floor = make_floor()
    player = make_player(tile=(0, 0))
    player.move_speed = 1
    floor.players["p1"] = player
    engine = setup_engine(floor)

    engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 0, "target_r": 3}])

    all_positions = []
    for _ in range(5):
        result = engine.step([])
        all_positions.extend(e["tile"] for e in result.events if e["type"] == "position_update")

    assert len(all_positions) == 3  # 3 tiles away, 1 per tick
    assert all_positions[-1] == [0, 3]
