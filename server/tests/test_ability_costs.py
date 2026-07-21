"""Costs & gating: mana, hp and charge costs; the global cooldown; and the
monster resource economy that lets a caster monster run dry."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import xp_for_level
from gep.systems import abilities
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


def make_player(store, mana=1000, **skills):
    p = Player(id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
               hp=1000, max_hp=1000, mana=mana, max_mana=1000, weapon_id="unarmed", skills=Skills())
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def full_setup(store, floor):
    engine = TickEngine()
    effects_system.register(engine, floor, xp_rates=store.xp_rates, xp_table=store.xp_table,
                            monsters_cfg=store.monsters, rewards=store.rewards, conversions=store.conversions)
    abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                       combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                       xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                       conversions=store.conversions)
    return engine


def cast(engine, ability_id, q=0, r=0, pid="p1"):
    return engine.step([{"intent_type": "use_ability", "player_id": pid,
                         "ability_id": ability_id, "target_q": q, "target_r": r}]).events


def test_insufficient_mana_rejects_and_spends_nothing(store):
    floor = make_floor()
    p = make_player(store, mana=5, mana_attunement=10)   # radiant_mend costs 15
    floor.players["p1"] = p
    engine = full_setup(store, floor)
    events = cast(engine, "radiant_mend")
    assert any(e.get("reason") == "not enough mana" for e in events)
    assert p.mana == 5


def test_mana_is_spent_on_a_successful_cast(store):
    floor = make_floor()
    p = make_player(store, mana=100, mana_attunement=10)
    floor.players["p1"] = p
    engine = full_setup(store, floor)
    cast(engine, "radiant_mend")
    assert p.mana == 85                                   # 100 - 15


def test_charges_gate_and_refill(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    engine = full_setup(store, floor)
    # concussive_blast has 2 charges, cooldown_ticks 16 as the refill interval.
    cast(engine, "concussive_blast", 0, 3)
    assert p.ability_charges["concussive_blast"] == 1
    cast(engine, "concussive_blast", 0, 3)
    assert p.ability_charges["concussive_blast"] == 0
    reject = cast(engine, "concussive_blast", 0, 3)
    assert any(e.get("reason") == "no charges" for e in reject)
    # A charge returns 16 ticks after the first spend.
    for _ in range(17):
        engine.step([])
    assert p.ability_charges["concussive_blast"] >= 1


def test_global_cooldown_blocks_a_second_cast(store):
    floor = make_floor()
    p = make_player(store, strength=10, mana_attunement=10)
    floor.players["p1"] = p
    engine = full_setup(store, floor)
    cast(engine, "war_cry")                               # global_cooldown_ticks 2
    reject = cast(engine, "radiant_mend")                 # different ability, same GCD
    assert any(e.get("reason") == "global cooldown" for e in reject)


def test_monster_resource_economy_runs_dry(store):
    """A monster template with a small resource pool casts until it can no
    longer afford the ability, then stops -- the cost gate applies to monsters
    exactly as to players."""
    from gep.effects import Effect
    from gep.systems.abilities import register as register_abilities

    floor = make_floor()
    p = make_player(store)
    floor.players["p1"] = p
    m = roll_monster("m", store.monsters["cave_rat"], store.stat_scaling)
    m.tile = (0, 1)
    m.floor_number = 5
    # Give this instance a tiny pool: enough for one fireburst (mana 12), not two.
    m.mana = m.max_mana = 12
    floor.monsters["m"] = m
    engine = full_setup(store, floor)

    def monster_cast():
        return engine.step([]).events, engine

    # First monster ability resolves (affords 12); pool empties.
    from gep.actions import MONSTER_ABILITY
    engine.schedule(0, MONSTER_ABILITY, {"monster_id": "m", "ability_id": "fireburst",
                                         "target_q": 0, "target_r": 0})
    engine.step([])
    assert m.mana == 0
    hp_after_first = p.hp
    # Second request cannot be afforded -> dropped, player unharmed further.
    engine.schedule(0, MONSTER_ABILITY, {"monster_id": "m", "ability_id": "fireburst",
                                         "target_q": 0, "target_r": 0})
    engine.step([])
    assert p.hp == hp_after_first
