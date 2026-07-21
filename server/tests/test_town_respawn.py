"""Respawn anchor = last town visited, else floor 1. Covers the FloorManager
hook that records a town on arrival, the persistence round-trip, and the
respawn handler relocating a defeated player to the anchor."""
import pathlib
import types

import pytest

from gep import db
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.floor_manager import FloorManager
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.systems import respawn
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
RULESET = {"radius": 8, "resource_spawn_chance": 0.0, "resource_weights": [["iron_ore", 1]],
           "monster_spawn_count": 0, "monster_weights": [["cave_rat", 1]]}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def a_player():
    return Player(id="p1", name="Hero", tower_id="tower-a", floor_number=7, tile=(3, 3),
                  hp=1, max_hp=100, mana=0, max_mana=10, weapon_id="unarmed", skills=Skills())


def fake_floor(safe: bool, number: int, up_exit=(1, 2)):
    layout = types.SimpleNamespace(safe=safe, floor_number=number, up_exit=up_exit)
    return types.SimpleNamespace(layout=layout)


def test_record_town_sets_anchor_only_on_a_safe_floor():
    p = a_player()
    FloorManager._record_town(p, fake_floor(safe=False, number=4))
    assert (p.spawn_floor, p.spawn_tile) == (1, (0, 0))   # unchanged default

    FloorManager._record_town(p, fake_floor(safe=True, number=4, up_exit=(2, 5)))
    assert p.spawn_floor == 4 and p.spawn_tile == (2, 5)


def test_defeat_relocates_to_the_anchor(store):
    floor = FloorState.from_layout(generate_floor("tower-a", 7, "test", RULESET))
    p = a_player()
    p.spawn_floor, p.spawn_tile = 3, (2, 5)               # a town visited earlier
    p.hp = 0.0
    p.alive = False
    floor.players["p1"] = p

    relocations = []
    engine = TickEngine()
    respawn.register(engine, floor, save_player=None,
                     on_relocate=lambda pid, fl, tile: relocations.append((pid, fl, tile)))
    engine.schedule(0, "player-defeated", {"player_id": "p1", "killer_id": "m"})
    engine.step([])

    assert relocations == [("p1", 3, (2, 5))]             # sent to the anchor floor
    assert p.alive and p.hp == p.max_hp                    # and restored


def test_anchor_persists_across_a_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "players.db")
    p = a_player()
    p.id = "persist-test"
    p.spawn_floor, p.spawn_tile = 5, (4, 4)
    db.save_player(p)
    loaded = db.load_player("persist-test")
    assert loaded["spawn_floor"] == 5
    assert loaded["spawn_tile"] == [4, 4]
