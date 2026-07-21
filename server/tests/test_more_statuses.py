"""The added status vocabulary: silence, disarm, invulnerable, fortify,
vulnerability, haste, taunt, cleanse."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.effects import Effect
from gep import effects as fx
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
    interrupt = abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                                   combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                                   xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                                   on_threat=notify, conversions=store.conversions)
    movement.register(engine, floor, on_move=interrupt)
    return engine


def test_silence_blocks_casting_only(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    add_monster(store, floor, "m", (0, 1))
    engine = full_setup(store, floor)
    p.active_effects.append(Effect(effect_id="s", kind="silence", expires_tick=99))

    cast = engine.step([{"intent_type": "use_ability", "player_id": "p1",
                         "ability_id": "fireburst", "target_q": 0, "target_r": 1}]).events
    assert any(e.get("reason") == "silenced" for e in cast)
    # But a weapon attack still works.
    attack = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m"}]).events
    assert any(e.get("type") == "engagement_started" for e in attack)


def test_disarm_blocks_attacking_only(store):
    floor = make_floor()
    p = make_player(store, arcana=10)
    floor.players["p1"] = p
    add_monster(store, floor, "m", (0, 1))
    engine = full_setup(store, floor)
    p.active_effects.append(Effect(effect_id="d", kind="disarm", expires_tick=99))

    attack = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m"}]).events
    assert any(e.get("reason") == "disarmed" for e in attack)
    # But casting still works.
    cast = engine.step([{"intent_type": "use_ability", "player_id": "p1",
                         "ability_id": "fireburst", "target_q": 0, "target_r": 1}]).events
    assert any(e.get("type") == "ability_used" for e in cast)


def test_invulnerable_negates_all_damage(store):
    p = make_player(store)
    p.active_effects.append(Effect(effect_id="i", kind="invulnerable", expires_tick=99))
    absorbed = p.take_damage(500)
    assert p.hp == 1000 and absorbed == 500


def test_fortify_and_vulnerability_scale_damage(store):
    fort = make_player(store)
    fort.active_effects.append(Effect(effect_id="f", kind="fortify", expires_tick=99, magnitude=0.25))
    fort.take_damage(100)
    assert fort.hp == 1000 - 75          # 25% reduced

    vuln = make_player(store)
    vuln.active_effects.append(Effect(effect_id="v", kind="vulnerability", expires_tick=99, magnitude=0.5))
    vuln.take_damage(100)
    assert vuln.hp == 1000 - 150         # 50% amplified


def test_haste_shortens_monster_cadence():
    slow = fx.pace_factor([Effect(effect_id="s", kind="slow", expires_tick=9, slow_fraction=0.5)])
    haste = fx.pace_factor([Effect(effect_id="h", kind="haste", expires_tick=9, haste_fraction=0.5)])
    assert slow > 1.0 and haste < 1.0


def test_taunt_forces_the_monster_onto_its_caster(store):
    floor = make_floor()
    puller = make_player(store, tile=(0, 1))
    floor.players["p1"] = puller
    other = make_player(store, tile=(0, 6))
    other.id = "p2"
    floor.players["p2"] = other
    m = add_monster(store, floor, "m", (0, 4))
    m.threat_table = {"p2": 99.0}         # would otherwise chase p2
    engine = full_setup(store, floor)
    m.active_effects.append(Effect(effect_id="t", kind="taunt", expires_tick=999, source_id="p1"))

    start = m.tile
    for _ in range(8):
        engine.step([])
    # The taunt drags it toward the puller at (0,1), i.e. its q/r fall, not
    # toward p2 at (0,6).
    assert m.tile != start
    assert abs(m.tile[1] - 1) < abs(start[1] - 1)


def test_cleanse_strips_debuffs_but_keeps_buffs(store):
    p = make_player(store)
    p.active_effects.extend([
        Effect(effect_id="poison", kind="dot", expires_tick=99, tick_amount=1, interval=1, next_tick=1),
        Effect(effect_id="weak", kind="debuff", expires_tick=99, stat="strength", magnitude=3),
        Effect(effect_id="rage", kind="buff", expires_tick=99, stat="strength", magnitude=5),
        Effect(effect_id="ward", kind="shield", expires_tick=99, absorb_remaining=50),
    ])
    removed = fx.cleanse(p.active_effects)
    kinds = {e.kind for e in p.active_effects}
    assert set(removed) == {"poison", "weak"}
    assert kinds == {"buff", "shield"}
