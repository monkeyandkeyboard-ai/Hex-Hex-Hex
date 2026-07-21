"""The timed-effects substrate: the per-tick sweep that ticks dots/hots,
spends and expires shields, and credits a dot's source on a kill."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.effects import Effect
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import xp_for_level
from gep.systems import effects as effects_system
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8, "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0, "monster_weights": [["cave_rat", 1]],
}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def make_player(store, **skills):
    p = Player(id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
               hp=1000, max_hp=1000, mana=1000, max_mana=1000,
               weapon_id="unarmed", skills=Skills())
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def add_monster(store, floor, mid, tile=(0, 1), hp=None):
    m = roll_monster(mid, store.monsters["cave_rat"], store.stat_scaling)
    m.tile = tile
    m.floor_number = 5
    if hp is not None:
        m.hp = hp
    floor.monsters[mid] = m
    return m


def setup(store, floor):
    engine = TickEngine()
    effects_system.register(
        engine, floor, xp_rates=store.xp_rates, xp_table=store.xp_table,
        monsters_cfg=store.monsters, rewards=store.rewards, conversions=store.conversions,
    )
    return engine


def test_dot_ticks_on_interval_and_expires(store):
    floor = make_floor()
    m = add_monster(store, floor, "m")
    engine = setup(store, floor)
    # 20 damage every 2 ticks, for 4 ticks -> ticks at t=2 and t=4, then gone.
    m.active_effects.append(Effect(effect_id="poison", kind="dot", expires_tick=4,
                                   source_id="p1", tick_amount=20.0, interval=2, next_tick=2))
    start = m.hp
    engine.step([])                       # t=1: nothing
    assert m.hp == start
    engine.step([])                       # t=2: -20
    assert m.hp == start - 20
    engine.step([])                       # t=3: nothing
    assert m.hp == start - 20
    events = engine.step([]).events       # t=4: -20 then expire
    assert m.hp == start - 40
    assert any(e["type"] == "effect_expired" and e["effect_id"] == "poison" for e in events)
    assert m.active_effects == []


def test_hot_heals_and_clamps_at_max_hp(store):
    floor = make_floor()
    p = make_player(store)
    p.hp = 900
    floor.players["p1"] = p
    engine = setup(store, floor)
    p.active_effects.append(Effect(effect_id="regen", kind="hot", expires_tick=6,
                                   source_id="p1", tick_amount=80.0, interval=1, next_tick=1))
    engine.step([])                       # 900 -> 980
    assert p.hp == 980
    engine.step([])                       # 980 -> clamps at 1000, not 1060
    assert p.hp == 1000


def test_shield_absorbs_before_hp_then_expires(store):
    floor = make_floor()
    p = make_player(store)
    floor.players["p1"] = p
    engine = setup(store, floor)
    p.active_effects.append(Effect(effect_id="ward", kind="shield", expires_tick=99,
                                   source_id="p1", absorb_remaining=50.0))
    # 30 damage: fully absorbed, HP untouched, shield 20 left.
    absorbed = p.take_damage(30)
    assert absorbed == 30 and p.hp == 1000
    assert p.active_effects[0].absorb_remaining == 20
    # 35 more: 20 absorbed, 15 to HP; shield now depleted.
    p.take_damage(35)
    assert p.hp == 985
    events = engine.step([]).events       # sweep drops the depleted shield
    assert any(e["type"] == "effect_expired" and e["effect_id"] == "ward" for e in events)
    assert p.active_effects == []


def test_dot_kill_pays_out_to_source_player(store):
    floor = make_floor()
    p = make_player(store)
    floor.players["p1"] = p
    m = add_monster(store, floor, "m", hp=5.0)
    engine = setup(store, floor)
    m.active_effects.append(Effect(effect_id="poison", kind="dot", expires_tick=10,
                                   source_id="p1", tick_amount=99.0, interval=1, next_tick=1,
                                   train_power={"arcana": 1.0}))
    events = engine.step([]).events
    assert not m.alive
    # Same payout a swing would produce: a death event and a respawn scheduled.
    assert any(e["type"] == "monster_died" and e["monster_id"] == "m" for e in events)
    # The corpse's effects were cleared so a second tick cannot pay out again.
    assert m.active_effects == []


def test_effects_are_transient_not_persisted():
    """Active effects follow the ability_cooldowns precedent -- never saved. A
    fresh Player defaults to none, and save_player's persisted column set has no
    slot for them, so a mid-flight effect cannot survive a logout."""
    import inspect

    from gep import db
    from gep.entities import Player

    fresh = Player(id="p", name="n", tower_id="t", floor_number=1, tile=(0, 0),
                   hp=1, max_hp=1, mana=0, max_mana=0, weapon_id="unarmed")
    assert fresh.active_effects == []
    assert "active_effects" not in inspect.getsource(db.save_player)
