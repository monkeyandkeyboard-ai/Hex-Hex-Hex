import pathlib

import pytest

import gep.combat as combat_mod
from gep.combat import resolve_attack
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_player(**stat_overrides):
    skills = Skills()
    skills.combat.update(stat_overrides)
    return Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=1000, max_hp=1000, mana=50, max_mana=50, weapon_id="fists", skills=skills,
    )


def force_rolls(monkeypatch, *rolls):
    it = iter(rolls)
    monkeypatch.setattr(combat_mod.random, "random", lambda: next(it))


def test_dodge_never_damages_target(store, monkeypatch):
    force_rolls(monkeypatch, 0.0)  # dodge-roll 0.0 always triggers dodge
    attacker = make_player(precision=10, strength=10)
    target = make_player(dexterity=50)
    result = resolve_attack(attacker, target, 10.0, "physical", store.combat_constants)
    assert result["result"] == "dodge"
    assert target.hp == 1000


def test_high_precision_beats_low_evasion(store, monkeypatch):
    monkeypatch.setattr(combat_mod.random, "random", lambda: 0.4)
    attacker = make_player(precision=80, strength=20)
    target = make_player(dexterity=1, constitution=1)
    result = resolve_attack(attacker, target, 10.0, "physical", store.combat_constants)
    assert result["result"] == "hit"


def test_final_damage_matches_formula(store, monkeypatch):
    c = store.combat_constants
    attacker = make_player(precision=1000, strength=40)
    target = make_player(dexterity=0, constitution=10)

    force_rolls(monkeypatch, 0.99, 0.0)  # fail dodge, guarantee hit

    result = resolve_attack(attacker, target, 10.0, "physical", c)
    assert result["result"] == "hit"

    a_damage_multiplier = 1 + 40 / c["constant_divisor"]
    base_damage = 10.0 * a_damage_multiplier
    effective_mitigation = 10 * c["damage_type_weighting"]["physical"]
    mitigation_pct = effective_mitigation / (effective_mitigation + c["defense_soft_cap_factor"])
    expected_damage = base_damage * (1 - mitigation_pct)

    assert result["damage"] == pytest.approx(expected_damage)
    assert target.hp == pytest.approx(1000 - expected_damage)


def test_target_dies_at_zero_hp(store, monkeypatch):
    attacker = make_player(precision=1000, strength=1000)
    target = make_player(dexterity=0, constitution=0)
    target.hp = 1

    force_rolls(monkeypatch, 0.99, 0.0)

    result = resolve_attack(attacker, target, 999.0, "physical", store.combat_constants)
    assert result["target_alive"] is False
    assert target.hp == 0


def test_arcana_damage_uses_potency_not_strength(store, monkeypatch):
    c = store.combat_constants
    attacker = make_player(precision=1000, strength=0, arcana=40)
    target = make_player(dexterity=0, constitution=0)

    force_rolls(monkeypatch, 0.99, 0.0)

    result = resolve_attack(attacker, target, 10.0, "magical", c)
    a_potency = 1 + (40 ** 1.1) / c["constant_divisor"]
    expected_base = 10.0 * a_potency
    assert result["damage"] == pytest.approx(expected_base)


def test_monster_roll_stays_within_configured_variance(store):
    template = store.monsters["goblin_skirmisher"]
    monster = roll_monster("m1", template, store.stat_scaling)
    for skill, block in template["skills"].items():
        lo = block["base"] + block["minrandom"]
        hi = block["base"] + block["maxrandom"]
        assert lo <= monster.stats[skill] <= hi, f"{skill} out of range"
    expected_max_hp = store.stat_scaling["hp_base"] + monster.stats["constitution"] * store.stat_scaling["hp_per_con"]
    assert monster.max_hp == pytest.approx(expected_max_hp)


def test_config_constants_match_real_values(store):
    c = store.combat_constants
    assert c["hit_chance_base"] == 0.5
    assert c["hit_chance_cap"] == 0.95
    assert c["constant_c"] == 10
    assert c["constant_divisor"] == 1
    assert c["defense_soft_cap_factor"] == 500


def test_elemental_types_share_the_configured_weighting(store):
    """fire/ice/electric are config entries, not special-cased names."""
    weighting = store.combat_constants["damage_type_weighting"]
    assert weighting["physical"] == 1.0
    assert weighting["magical"] == 0.5
    assert weighting["fire"] == weighting["ice"] == weighting["electric"] == 0.75


@pytest.mark.parametrize("damage_type", ["magical", "fire", "ice", "electric"])
def test_weight_is_coefficient_on_mitigation_before_soft_cap(store, monkeypatch, damage_type):
    """pct = (con * weight) / (con * weight + cap), not weight applied after."""
    c = store.combat_constants
    con = 400
    attacker = make_player(precision=1000, strength=0, arcana=0)
    target = make_player(dexterity=0, constitution=con)
    force_rolls(monkeypatch, 0.99, 0.0)

    result = resolve_attack(attacker, target, 100.0, damage_type, c)

    weight = c["damage_type_weighting"][damage_type]
    effective = con * weight
    pct = effective / (effective + c["defense_soft_cap_factor"])
    # arcana=0 and strength=0 make both scaling multipliers 1.0, isolating mitigation
    assert result["damage"] == pytest.approx(100.0 * (1 - pct))


def test_unknown_damage_type_falls_back_to_default_not_unmitigated(store, monkeypatch):
    """A flavour word like the "Unarmed" in weapons/fists.json must not slip
    through a dict miss and collect the 1.0 weighting by accident."""
    c = store.combat_constants
    con = 400
    target_a = make_player(dexterity=0, constitution=con)
    target_b = make_player(dexterity=0, constitution=con)
    attacker = make_player(precision=1000, strength=0, arcana=0)

    force_rolls(monkeypatch, 0.99, 0.0)
    unknown = resolve_attack(attacker, target_a, 100.0, "Unarmed", c)
    force_rolls(monkeypatch, 0.99, 0.0)
    physical = resolve_attack(attacker, target_b, 100.0, "physical", c)

    assert unknown["damage"] == pytest.approx(physical["damage"])


def test_damage_type_is_case_insensitive(store, monkeypatch):
    c = store.combat_constants
    a = make_player(precision=1000, strength=0, arcana=0)
    force_rolls(monkeypatch, 0.99, 0.0)
    hot = resolve_attack(a, make_player(dexterity=0, constitution=400), 100.0, "FIRE", c)
    force_rolls(monkeypatch, 0.99, 0.0)
    cold = resolve_attack(a, make_player(dexterity=0, constitution=400), 100.0, "fire", c)
    assert hot["damage"] == pytest.approx(cold["damage"])
