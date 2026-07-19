"""Weapon damage: power (stat-driven, per weapon class) times the equipped
weapon's damage_min/max multiplier. See power_scaling.json and
weapon_classes.json, and combat_system.compute_power/weapon_profile.
"""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floor_state import FloorState
from gep.floorgen import generate_floor
from gep.systems import combat_system

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


def make_player(weapon_id, tile=(0, 0)):
    player = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=10_000, max_hp=10_000, mana=0, max_mana=0, weapon_id=weapon_id,
        skills=Skills(),
    )
    player.skills.combat["precision"] = 400   # never miss
    return player


def setup(store, floor, player):
    monster = roll_monster("m1", store.monsters["cave_rat"], store.stat_scaling)
    monster.tile = (0, 1)
    monster.floor_number = 5
    monster.hp = monster.max_hp = 10_000_000
    monster.stats["dexterity"] = 0            # never dodge
    floor.monsters["m1"] = monster
    floor.players[player.id] = player

    engine = combat_system_engine()
    combat_system.register(
        engine, floor,
        weapons=store.weapons, monsters_cfg=store.monsters,
        combat_constants=store.combat_constants, xp_rates=store.xp_rates,
        xp_table=store.xp_table, stat_scaling=store.stat_scaling,
        rewards=store.rewards, items=store.items,
        weapon_classes=store.weapon_classes, power_scaling=store.power_scaling,
    )
    return engine, monster


def combat_system_engine():
    from gep.tick import TickEngine
    return TickEngine()


def swing_once(engine):
    result = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m1"}])
    return next(e for e in result.events if e["type"] == "combat_result")


def test_melee_power_ignores_dexterity_and_arcana(store):
    """power_scaling.json: melee's only contributor is strength. Piling
    dexterity and arcana onto a melee-class weapon's wielder must not change
    the computed power at all."""
    scaling = store.power_scaling["melee"]["stats"]
    assert set(scaling) == {"strength"}

    player = make_player("unarmed")
    player.skills.combat.update({"strength": 100, "dexterity": 999, "arcana": 999})
    power = sum(player.combat_stat(stat) * coeff for stat, coeff in scaling.items())
    assert power == 100


def test_zero_damage_min_can_whiff_for_zero(store):
    """Unarmed's damage_min is 0: a swing can legitimately land for 0 damage,
    a hit with nothing behind it -- distinct from a miss or dodge.

    combat_system.py and combat.py both call the same shared `random`
    module's random(), in this order per swing: the damage-multiplier roll,
    then evasion, then the hit check. One patch covers all three.
    """
    import random as random_mod

    floor = make_floor()
    player = make_player("unarmed")
    player.skills.combat["strength"] = 100
    engine, monster = setup(store, floor, player)

    original = random_mod.random
    rolls = iter([0.0, 0.99, 0.0])   # damage roll at min, no dodge, hit
    random_mod.random = lambda: next(rolls)
    try:
        result = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m1"}])
    finally:
        random_mod.random = original

    hit = next(e for e in result.events if e["type"] == "combat_result")
    assert hit["result"] == "hit"
    assert hit["damage"] == 0.0


def test_weapon_class_drives_which_stat_scales_damage(store):
    """Unarmed is melee-class (strength-scaled per weapon_classes.json /
    power_scaling.json): a player stacked on arcana instead of strength must
    swing for less, since arcana contributes nothing to melee power."""
    import random as random_mod

    def deterministic_swing(strength, arcana):
        floor = make_floor()
        player = make_player("unarmed")
        player.skills.combat.update({"strength": strength, "arcana": arcana})
        engine, _ = setup(store, floor, player)

        original = random_mod.random
        # Damage roll at the top, no dodge, guaranteed hit.
        rolls = iter([0.999999, 0.99, 0.0])
        random_mod.random = lambda: next(rolls)
        try:
            result = engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": "m1"}])
        finally:
            random_mod.random = original
        return next(e for e in result.events if e["type"] == "combat_result")

    strong_hit = deterministic_swing(strength=200, arcana=1)
    weak_hit = deterministic_swing(strength=1, arcana=200)

    assert strong_hit["result"] == weak_hit["result"] == "hit"
    assert strong_hit["damage"] > weak_hit["damage"]


def test_every_shipped_weapon_type_resolves_a_class_and_damage_type(store):
    for weapon_type, weapon_class in store.weapon_classes.items():
        if weapon_type.startswith("_"):
            continue
        assert weapon_class in store.power_scaling
        assert store.power_scaling[weapon_class]["damage_type"] in \
            store.combat_constants["damage_type_weighting"]


def test_every_main_hand_item_base_type_is_a_known_weapon_class(store):
    for code, base in store.item_bases.items():
        if base["equipment_slot"] in ("main_hand", "two_hand"):
            assert base["type"] in store.weapon_classes, f"{code}: {base['type']!r}"
