"""Derived combat stats (crit, leech, thorns, damage_reduction, cooldown
reduction): all dormant at 0, and read through entity.derived_stat so a timed
buff can grant one exactly as gear would. Granting them here via a buff effect
exercises the same read path a rolled affix would feed."""
import pathlib
import random

import pytest

from gep.combat import resolve_attack
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


def make_player(store, **skills):
    p = Player(id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
               hp=1000, max_hp=1000, mana=1000, max_mana=1000, weapon_id="unarmed", skills=Skills())
    p.skills.combat["precision"] = 400   # so direct resolve_attack lands
    p.skills.combat.update(skills)
    for skill, level in p.skills.combat.items():
        p.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    p.refresh_stats(store.items)
    return p


def grant(entity, stat, magnitude):
    entity.active_effects.append(Effect(effect_id="grant:" + stat, kind="buff",
                                        expires_tick=10**9, stat=stat, magnitude=magnitude))


def a_monster(store, hp=10_000_000):
    m = roll_monster("m", store.monsters["cave_rat"], store.stat_scaling)
    m.tile = (0, 1)
    m.hp = m.max_hp = hp
    return m


def test_crit_dormant_by_default_and_fires_when_granted(store):
    const = store.combat_constants
    random.seed(4)
    plain = resolve_attack(make_player(store, strength=50), a_monster(store), 100, "physical", const)
    assert plain["result"] == "hit" and plain["crit"] is False

    crit_player = make_player(store, strength=50)
    grant(crit_player, "critical_strike_chance", 1.0)   # guaranteed crit
    random.seed(4)
    crit = resolve_attack(crit_player, a_monster(store), 100, "physical", const)
    assert crit["crit"] is True
    assert crit["damage"] > plain["damage"]              # crit multiplier applied


def test_life_leech_heals_the_attacker(store):
    p = make_player(store, strength=50)
    p.hp = 500
    grant(p, "life_leech", 100)                           # heal 100% of damage dealt
    random.seed(1)
    result = resolve_attack(p, a_monster(store), 100, "physical", store.combat_constants)
    assert result["result"] == "hit"
    assert result["leeched"] > 0 and p.hp > 500


def test_thorns_reflects_to_the_attacker(store):
    p = make_player(store, strength=50)
    p.hp = 500
    m = a_monster(store)
    grant(m, "thorns", 25)
    random.seed(1)
    result = resolve_attack(p, m, 100, "physical", store.combat_constants)
    assert result["thorns"] == 25
    assert p.hp == 475                                    # flat 25 reflected


def test_damage_reduction_lowers_damage_taken(store):
    p = make_player(store)
    grant(p, "damage_reduction", 60)                      # 60% less
    p.take_damage(100)
    assert p.hp == 1000 - 40


def test_armor_reduces_damage_taken(store):
    const = store.combat_constants
    bare = a_monster(store)
    armored = a_monster(store)
    grant(armored, "armor", 2000)                        # folds into mitigation
    random.seed(2)
    d1 = resolve_attack(make_player(store, strength=50), bare, 500, "physical", const)
    random.seed(2)
    d2 = resolve_attack(make_player(store, strength=50), armored, 500, "physical", const)
    assert d1["result"] == "hit" and d2["result"] == "hit"
    assert d2["damage"] < d1["damage"]


def test_resistance_reduces_only_its_damage_type(store):
    # One player and one monster throughout, so constitution variance can't
    # skew the before/after comparison -- only the resistance changes.
    const = store.combat_constants
    p = make_player(store, arcana=50, strength=50)
    m = a_monster(store)
    random.seed(5); phys_before = resolve_attack(p, m, 400, "physical", const)
    random.seed(5); fire_before = resolve_attack(p, m, 400, "fire", const)
    grant(m, "fire_resistance", 50)                       # -50% fire only
    random.seed(5); fire_after = resolve_attack(p, m, 400, "fire", const)
    random.seed(5); phys_after = resolve_attack(p, m, 400, "physical", const)
    assert fire_after["damage"] == pytest.approx(fire_before["damage"] * 0.5, rel=1e-6)
    assert phys_after["damage"] == pytest.approx(phys_before["damage"], rel=1e-6)


def test_pre_scaled_attacks_do_not_double_count_the_stat(store):
    """A player swing/ability passes apply_stat_scaling=False (power already
    folded the stat in); the same weapon_damage then lands the same regardless
    of attacker strength. A monster basic strike (True) still scales."""
    const = store.combat_constants
    weak = make_player(store, strength=1)
    strong = make_player(store, strength=999)
    random.seed(7)
    d_weak = resolve_attack(weak, a_monster(store), 100, "physical", const, apply_stat_scaling=False)
    random.seed(7)
    d_strong = resolve_attack(strong, a_monster(store), 100, "physical", const, apply_stat_scaling=False)
    assert d_weak["damage"] == pytest.approx(d_strong["damage"], rel=1e-6)
    # With scaling on (a monster strike), strength does raise damage.
    random.seed(7)
    d_scaled = resolve_attack(strong, a_monster(store), 100, "physical", const, apply_stat_scaling=True)
    assert d_scaled["damage"] > d_strong["damage"]


def test_cooldown_reduction_shortens_ability_cooldown(store):
    floor = FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))
    p = make_player(store, arcana=10)                     # knows fireburst (cd 8)
    grant(p, "cooldown_reduction", 50)
    floor.players["p1"] = p
    engine = TickEngine()
    effects_system.register(engine, floor, xp_rates=store.xp_rates, xp_table=store.xp_table,
                            monsters_cfg=store.monsters, rewards=store.rewards, conversions=store.conversions)
    abilities.register(engine, floor, abilities_cfg=store.abilities, monsters_cfg=store.monsters,
                       combat_constants=store.combat_constants, xp_rates=store.xp_rates,
                       xp_table=store.xp_table, rewards=store.rewards, items=store.items,
                       conversions=store.conversions)
    engine.step([{"intent_type": "use_ability", "player_id": "p1",
                  "ability_id": "fireburst", "target_q": 0, "target_r": 1}])
    # 8-tick cooldown halved to 4, from tick 1 -> ready at tick 5.
    assert p.ability_cooldowns["fireburst"] == 1 + 4
