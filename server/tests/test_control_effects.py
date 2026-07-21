"""Control effects gate what an entity may do: a stun stops attacks, casts and
movement; a root stops only movement; a slow stretches movement cadence. The
gating is read from gep.effects by each system's handlers."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.effects import Effect
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import xp_for_level
from gep.systems import abilities, combat_system, monster_ai, movement
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


def make_player(store, tile=(0, 0), **skills):
    p = Player(id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
               hp=1000, max_hp=1000, mana=1000, max_mana=1000, weapon_id="unarmed", skills=Skills())
    p.skills.combat["precision"] = 400
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def add_monster(store, floor, mid, tile=(0, 1)):
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
    notify = monster_ai.register(engine, floor, monsters_cfg=store.monsters, abilities_cfg=store.abilities)
    combat_system.register(engine, floor, weapons=store.weapons, monsters_cfg=store.monsters,
                           combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                           xp_table=store.xp_table, stat_scaling=store.stat_scaling, rewards=store.rewards,
                           items=store.items, weapon_classes=store.weapon_classes,
                           power_scaling=store.power_scaling, on_threat=notify, conversions=store.conversions)
    abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                       combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                       xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                       on_threat=notify, conversions=store.conversions)
    movement.register(engine, floor)
    return engine


def test_stunned_player_cannot_attack_or_move(store):
    floor = make_floor()
    p = make_player(store)
    floor.players["p1"] = p
    add_monster(store, floor, "m", (0, 1))
    engine = full_setup(store, floor)
    p.active_effects.append(Effect(effect_id="s", kind="stun", expires_tick=99))

    attack = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m"}]).events
    assert any(e.get("reason") == "stunned" for e in attack)
    move = engine.step([{"intent_type": "move-to-tile", "player_id": "p1",
                         "target_q": 3, "target_r": 0}]).events
    assert any(e.get("reason") == "immobilized" for e in move)
    assert p.tile == (0, 0)


def test_rooted_player_can_attack_but_not_move(store):
    floor = make_floor()
    p = make_player(store, strength=50)
    floor.players["p1"] = p
    add_monster(store, floor, "m", (0, 1))
    engine = full_setup(store, floor)
    p.active_effects.append(Effect(effect_id="r", kind="root", expires_tick=99))

    attack = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m"}]).events
    assert any(e.get("type") == "engagement_started" for e in attack)
    move = engine.step([{"intent_type": "move-to-tile", "player_id": "p1",
                         "target_q": 3, "target_r": 0}]).events
    assert any(e.get("reason") == "immobilized" for e in move)
    assert p.tile == (0, 0)


def test_stunned_monster_does_not_act(store):
    floor = make_floor()
    p = make_player(store)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", (0, 2))
    m.threat_table = {"p1": 5.0}          # already hunting the player
    engine = full_setup(store, floor)
    m.active_effects.append(Effect(effect_id="s", kind="stun", expires_tick=99))

    start = m.tile
    for _ in range(6):
        engine.step([])
    # A stunned monster neither steps toward the player nor strikes it.
    assert m.tile == start
    assert p.hp == p.max_hp


def test_slow_stretches_player_step_cadence(store):
    floor = make_floor()
    fast = make_player(store, tile=(0, 0))
    floor.players["p1"] = fast
    engine = full_setup(store, floor)
    fast.active_effects.append(Effect(effect_id="sl", kind="slow", expires_tick=999, slow_fraction=0.5))

    # slow 0.5 -> a step every 2 ticks instead of every tick. Path of 4 tiles
    # therefore takes noticeably longer than the 4 ticks it would unslowed.
    engine.step([{"intent_type": "move-to-tile", "player_id": "p1", "target_q": 0, "target_r": 4}])
    ticks = 1
    while fast.tile != (0, 4) and ticks < 30:
        engine.step([])
        ticks += 1
    assert fast.tile == (0, 4)
    assert ticks >= 7   # ~2 ticks per step, vs 4 unslowed
