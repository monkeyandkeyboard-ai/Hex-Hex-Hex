"""Out-of-combat regeneration."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import compute_hp_regen, compute_mana_regen
from gep.systems import regeneration
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def make_player(hp=100.0, mana=10.0, max_hp=500.0, max_mana=50.0):
    return Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
        hp=hp, max_hp=max_hp, mana=mana, max_mana=max_mana,
        weapon_id="unarmed", skills=Skills(),
    )


def setup(store, floor):
    engine = TickEngine()
    regeneration.register(engine, floor, store.stat_scaling)
    return engine


def test_health_and_mana_recover_each_tick(store):
    floor = make_floor()
    player = make_player(hp=100.0, mana=10.0)
    floor.players["p1"] = player
    engine = setup(store, floor)

    con = player.combat_stat("constitution")
    att = player.combat_stat("mana_attunement")
    hp_rate = compute_hp_regen(con, store.stat_scaling)
    mana_rate = compute_mana_regen(att, store.stat_scaling)

    engine.step([])
    assert player.hp == pytest.approx(100.0 + hp_rate)
    assert player.mana == pytest.approx(10.0 + mana_rate)

    engine.step([])
    assert player.hp == pytest.approx(100.0 + 2 * hp_rate)


def test_regeneration_is_clamped_to_the_maximum(store):
    floor = make_floor()
    player = make_player(hp=499.9, mana=49.9)
    floor.players["p1"] = player
    engine = setup(store, floor)

    for _ in range(20):
        engine.step([])

    assert player.hp == player.max_hp
    assert player.mana == player.max_mana


def test_the_dead_do_not_heal(store):
    floor = make_floor()
    player = make_player(hp=0.0)
    player.alive = False
    floor.players["p1"] = player
    engine = setup(store, floor)

    for _ in range(5):
        engine.step([])

    assert player.hp == 0.0


def test_monsters_regenerate_health_and_have_no_mana_pool(store):
    floor = make_floor()
    monster = roll_monster("m1", store.monsters["cave_rat"], store.stat_scaling)
    monster.hp = 1.0
    floor.monsters["m1"] = monster
    engine = setup(store, floor)

    engine.step([])

    assert monster.hp > 1.0
    assert not hasattr(monster, "mana"), "monsters should not grow a mana pool"


def test_regeneration_emits_no_events(store):
    """Vitality already rides the per-tick player_update broadcast; emitting
    here too would double the traffic for the same numbers."""
    floor = make_floor()
    floor.players["p1"] = make_player()
    engine = setup(store, floor)

    result = engine.step([])
    assert result.events == []


def test_configured_rates_are_what_actually_applies(store):
    """The curve is config, not a constant in the system."""
    ss = store.stat_scaling
    assert compute_hp_regen(1, ss) == pytest.approx(ss["hp_base"] * 0 + ss["hp_regen_base"] + ss["hp_regen_per_con"])
    assert compute_mana_regen(10, ss) == pytest.approx(
        ss["mana_regen_base"] + 10 * ss["mana_regen_per_mana_attunement"])
