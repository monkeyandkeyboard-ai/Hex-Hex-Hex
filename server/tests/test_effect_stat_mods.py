"""Timed buff/debuff/slow: they change what combat_stat reports for their
duration and then revert, without touching the equipment stat pipeline."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.effects import Effect
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


def make_player(store, **skills):
    p = Player(id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
               hp=1000, max_hp=1000, mana=1000, max_mana=1000, weapon_id="unarmed", skills=Skills())
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def full_setup(store, floor):
    engine = TickEngine()
    effects_system.register(engine, floor, xp_rates=store.xp_rates, xp_table=store.xp_table,
                            monsters_cfg=store.monsters, rewards=store.rewards,
                            conversions=store.conversions)
    abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                       combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                       xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                       conversions=store.conversions)
    return engine


def test_buff_raises_combat_stat_then_reverts(store):
    p = make_player(store, strength=10)
    base = p.combat_stat("strength")
    p.active_effects.append(Effect(effect_id="rage", kind="buff", expires_tick=99,
                                   stat="strength", magnitude=7))
    assert p.combat_stat("strength") == base + 7
    p.active_effects.clear()
    assert p.combat_stat("strength") == base


def test_debuff_lowers_combat_stat(store):
    p = make_player(store, dexterity=20)
    base = p.combat_stat("dexterity")
    p.active_effects.append(Effect(effect_id="curse", kind="debuff", expires_tick=99,
                                   stat="dexterity", magnitude=5))
    assert p.combat_stat("dexterity") == base - 5


def test_war_cry_buff_applies_via_cast_and_expires(store):
    floor = make_floor()
    p = make_player(store, strength=10, mana_attunement=10)
    floor.players["p1"] = p
    engine = full_setup(store, floor)
    base = p.combat_stat("strength")

    engine.step([{"intent_type": "use_ability", "player_id": "p1",
                  "ability_id": "war_cry", "target_q": 0, "target_r": 0}])
    # Buff (+5 strength) and shield (30 absorb) both landed on the caster.
    assert p.combat_stat("strength") == base + 5
    assert any(e.kind == "shield" and e.absorb_remaining == 30 for e in p.active_effects)

    # war_cry lasts 15 ticks; after they elapse the buff is gone and reverted.
    for _ in range(16):
        engine.step([])
    assert p.combat_stat("strength") == base
    assert all(e.kind != "buff" for e in p.active_effects)
