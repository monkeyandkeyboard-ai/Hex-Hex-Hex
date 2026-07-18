import pathlib

import pytest

import gep.combat as combat_mod
from gep.combat import resolve_attack
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.prng import Mulberry32

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_player(**stat_overrides):
    skills = Skills()
    skills.combat.update(stat_overrides)
    return Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=20, max_mana=20, weapon_id="fists", skills=skills,
    )


def force_rolls(monkeypatch, *rolls):
    """Force combat_mod.random.random() to yield exactly these values in
    order (dodge-check roll, then hit-check roll)."""
    it = iter(rolls)
    monkeypatch.setattr(combat_mod.random, "random", lambda: next(it))


def test_dodge_never_damages_target(store, monkeypatch):
    force_rolls(monkeypatch, 0.0)  # dodge-roll 0.0 always beats a positive evasion chance
    attacker = make_player(precision=10, strength=10)
    target = make_player(dexterity=50)
    result = resolve_attack(attacker, target, 5, "physical", store.combat_constants)
    assert result["result"] == "dodge"
    assert target.hp == 100


def test_high_precision_beats_low_evasion_for_a_hit(store, monkeypatch):
    monkeypatch.setattr(combat_mod.random, "random", lambda: 0.4)
    attacker = make_player(precision=80, strength=20)
    target = make_player(dexterity=1, constitution=1)
    result = resolve_attack(attacker, target, 10, "physical", store.combat_constants)
    assert result["result"] == "hit"


def test_final_damage_reduces_target_hp_and_matches_formula(store, monkeypatch):
    c = store.combat_constants
    attacker = make_player(precision=1000, strength=40)
    target = make_player(dexterity=0, constitution=10)

    force_rolls(monkeypatch, 0.99, 0.0)  # fail dodge (roll high), guarantee hit (roll low)

    result = resolve_attack(attacker, target, 10, "physical", c)
    assert result["result"] == "hit"

    a_damage_multiplier = 1 + 40 / c["strength_damage_divisor"]
    base_damage = 10 * a_damage_multiplier
    t_raw_mitigation = 10 * c["mitigation_multiplier"]
    effective_mitigation = t_raw_mitigation * c["damage_type_weighting"]["physical"]
    mitigation_pct = effective_mitigation / (effective_mitigation + c["defense_soft_cap_factor"])
    expected_damage = base_damage * (1 - mitigation_pct)

    assert result["damage"] == pytest.approx(expected_damage)
    assert target.hp == pytest.approx(100 - expected_damage)


def test_target_dies_at_zero_hp(store, monkeypatch):
    attacker = make_player(precision=1000, strength=1000)
    target = make_player(dexterity=0, constitution=0)
    target.hp = 1

    force_rolls(monkeypatch, 0.99, 0.0)

    result = resolve_attack(attacker, target, 999, "physical", store.combat_constants)
    assert result["target_alive"] is False
    assert target.hp == 0


def test_arcana_damage_uses_potency_not_strength(store, monkeypatch):
    c = store.combat_constants
    attacker = make_player(precision=1000, strength=0, arcana=40)
    target = make_player(dexterity=0, constitution=0)

    force_rolls(monkeypatch, 0.99, 0.0)

    result = resolve_attack(attacker, target, 10, "arcana", c)
    a_potency = 1 + (40 ** c["arcana_potency_exponent"]) / c["arcana_potency_divisor"]
    expected_base = 10 * a_potency
    assert result["damage"] == pytest.approx(expected_base)  # no mitigation, con=0


def test_monster_roll_stays_within_configured_variance(store):
    template = store.monsters["goblin_skirmisher"]
    rng = Mulberry32(12345)
    monster = roll_monster("m1", template, rng, store.stat_scaling)
    for skill, rng_range in template["stats"].items():
        assert rng_range["min"] <= monster.stats[skill] <= rng_range["max"]
    assert monster.max_hp == pytest.approx(
        store.stat_scaling["max_hp_base"] + monster.stats["constitution"] * store.stat_scaling["max_hp_per_constitution"]
    )
