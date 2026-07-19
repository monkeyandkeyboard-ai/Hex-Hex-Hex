"""Equipment as a state machine rather than an armed/unarmed special case.

The property under test is uniformity: "nothing equipped" must be an ordinary
registry entry that combat resolves through the same lookup as a sword. If
someone reintroduces a hardcoded default, or teaches combat to branch on
whether a weapon is "real", these fail.
"""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.floor_state import FloorState
from gep.floorgen import generate_floor
from gep.systems import inventory_system
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 6,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_player(store):
    return Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 0),
        hp=100, max_hp=100, mana=0, max_mana=0,
        weapon_id=store.default_equipment_state, skills=Skills(),
    )


def setup(store):
    floor = FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))
    player = make_player(store)
    floor.players["p1"] = player
    engine = TickEngine()
    inventory_system.register(engine, floor, store.weapons,
                              store.default_equipment_state, store.items)
    return floor, player, engine


def test_unequipping_lands_in_the_configured_default_not_a_literal(store):
    floor, player, engine = setup(store)

    # Equip something, then take it off.
    player.equipment.main_hand = "unarmed"
    player.weapon_id = "unarmed"
    engine.step([{"intent_type": "unequip-item", "player_id": "p1",
                  "equip_slot": "main_hand"}])

    assert player.equipment.main_hand is None
    assert player.weapon_id == store.default_equipment_state


def test_default_state_is_resolvable_by_combat_like_any_other_entry(store):
    """The combat pipeline does weapons.get(player.weapon_id) with no special
    case, so the default state must be present in the registry with every
    parameter combat reads."""
    entry = store.weapons[store.default_equipment_state]
    for field in ("base_power", "cooldown_ticks", "damage_type"):
        assert field in entry, f"default state cannot supply {field} to combat"


def test_no_hardcoded_default_equipment_id_in_the_engine():
    """The default state's identity lives in config. If a literal creeps back
    into the engine, a config change stops being sufficient to move it."""
    gep = pathlib.Path(__file__).resolve().parents[1] / "gep"
    offenders = []
    for path in gep.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for literal in ('"fists"', "'fists'", '"unarmed"', "'unarmed'"):
            if literal in text:
                offenders.append(f"{path.name}: {literal}")
    assert not offenders, f"hardcoded equipment id in engine: {offenders}"


def test_identical_swings_deal_identical_damage(store):
    """base_power is a flat baseline: with the evasion and hit rolls forced to
    land, repeated swings must produce the exact same number. This is the
    property that replaces the old damage_min..damage_max roll."""
    import gep.combat as combat_mod
    from gep.combat import resolve_attack
    from gep.entities import roll_monster

    entry = store.weapons[store.default_equipment_state]
    attacker = make_player(store)
    attacker.skills.combat.update({"strength": 50, "precision": 400})

    # One fixed target: roll_monster randomizes constitution, and a different
    # mitigation value per iteration would mask what this is checking.
    target = roll_monster("m", store.monsters["cave_rat"], store.stat_scaling)
    target.hp = target.max_hp = 10_000_000

    damages = []
    for _ in range(5):
        # Force past evasion (high roll) and into a hit (low roll), isolating
        # the damage number from the two checks that are still random.
        original = combat_mod.random.random
        rolls = iter([0.99, 0.0])
        combat_mod.random.random = lambda: next(rolls)
        try:
            result = resolve_attack(attacker, target, entry["base_power"],
                                    entry["damage_type"], store.combat_constants)
        finally:
            combat_mod.random.random = original
        assert result["result"] == "hit"
        damages.append(result["damage"])

    assert len(set(damages)) == 1, f"damage varied across swings: {damages}"


def test_deprecated_range_fields_are_gone_from_equipment(store):
    """A leftover damage_min/damage_max would be silently ignored by the
    pipeline and misleading to anyone tuning the config."""
    for item_id, data in store.weapons.items():
        for dead in ("damage_min", "damage_max", "speed_ticks", "type"):
            assert dead not in data, f"{item_id} still carries {dead}"


def test_loader_rejects_a_non_numeric_base_power(tmp_path):
    import json
    import shutil
    from gep.config_loader import ConfigError
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    entry = tmp_path / "config" / "weapons" / "equipment_handler.json"
    data = json.loads(entry.read_text())
    data["base_power"] = "hard"
    entry.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="base_power"):
        ConfigStore(tmp_path / "config")


def test_loader_rejects_a_zero_cooldown(tmp_path):
    """Zero would swing every tick regardless of the equipment -- the pacing
    gate failing open, not a very fast weapon."""
    import json
    import shutil
    from gep.config_loader import ConfigError
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    entry = tmp_path / "config" / "weapons" / "equipment_handler.json"
    data = json.loads(entry.read_text())
    data["cooldown_ticks"] = 0
    entry.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="cooldown_ticks"):
        ConfigStore(tmp_path / "config")
