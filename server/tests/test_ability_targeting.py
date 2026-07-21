"""Targeting & delivery: friendly effects land on friends, offensive on
enemies (no friendly fire); cast time delays resolution and a move interrupts
it; a projectile delays impact by travel time."""
import pathlib
import random

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import xp_for_level
from gep.systems import abilities, movement
from gep.systems import effects as effects_system
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
RULESET = {"radius": 8, "resource_spawn_chance": 0.0, "resource_weights": [["iron_ore", 1]],
           "monster_spawn_count": 0, "monster_weights": [["cave_rat", 1]]}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def make_player(store, pid="p1", tile=(0, 0), **skills):
    p = Player(id=pid, name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
               hp=1000, max_hp=1000, mana=1000, max_mana=1000, weapon_id="unarmed", skills=Skills())
    p.skills.combat["precision"] = 400
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def add_monster(store, floor, mid, tile):
    m = roll_monster(mid, store.monsters["cave_rat"], store.stat_scaling)
    m.tile = tile
    m.floor_number = 5
    m.hp = m.max_hp = 10_000_000
    floor.monsters[mid] = m
    return m


def full_setup(store, floor):
    engine = TickEngine()
    effects_system.register(engine, floor, xp_rates=store.xp_rates, xp_table=store.xp_table,
                            monsters_cfg=store.monsters, rewards=store.rewards, conversions=store.conversions)
    interrupt = abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                                   combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                                   xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                                   conversions=store.conversions)
    movement.register(engine, floor, on_move=interrupt)
    return engine


def cast(engine, ability_id, q, r, pid="p1"):
    return engine.step([{"intent_type": "use_ability", "player_id": pid,
                         "ability_id": ability_id, "target_q": q, "target_r": r}]).events


def test_self_heal_restores_caster_and_ignores_enemies(store):
    floor = make_floor()
    p = make_player(store, mana_attunement=10)
    p.hp = 500
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 0))   # standing on the caster
    engine = full_setup(store, floor)

    cast(engine, "radiant_mend", 0, 0)
    assert p.hp == 540                            # +40 heal
    assert m.hp == m.max_hp                        # a friendly effect never hit the enemy


def test_target_enemy_damage_hits_the_enemy(store):
    floor = make_floor()
    p = make_player(store, strength=50)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 1))
    engine = full_setup(store, floor)

    random.seed(3)
    events = cast(engine, "heavy_strike", 0, 1)
    assert any(e.get("type") == "combat_result" and e["target"] == "m" for e in events)
    assert m.hp < m.max_hp


def test_cast_time_delays_resolution(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 3))
    engine = full_setup(store, floor)

    started = cast(engine, "concussive_blast", 0, 3)     # cast_ticks 2
    assert any(e.get("type") == "cast_started" for e in started)
    assert m.hp == m.max_hp                               # nothing yet
    engine.step([])                                       # t+1: still casting
    assert m.hp == m.max_hp
    random.seed(2)
    engine.step([])                                       # t+2: resolves
    assert m.hp < m.max_hp
    assert any(e.kind == "stun" for e in m.active_effects)


def test_moving_interrupts_a_cast(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 3))
    engine = full_setup(store, floor)

    cast(engine, "concussive_blast", 0, 3)               # begin a 2-tick cast
    interrupted = engine.step([{"intent_type": "move-to-tile", "player_id": "p1",
                                "target_q": 1, "target_r": 0}]).events
    assert any(e.get("type") == "cast_interrupted" for e in interrupted)
    engine.step([])                                       # would-be resolution tick
    assert m.hp == m.max_hp                               # cast never landed


def test_projectile_delays_impact_by_travel_time(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 3))            # distance 3, speed 3 -> 1 tick
    engine = full_setup(store, floor)

    fired = cast(engine, "venom_dart", 0, 3)
    assert any(e.get("type") == "projectile" for e in fired)
    assert m.hp == m.max_hp                               # in flight, no impact yet
    random.seed(1)
    engine.step([])                                       # projectile arrives
    assert m.hp < m.max_hp
    assert any(e.kind == "dot" for e in m.active_effects)
