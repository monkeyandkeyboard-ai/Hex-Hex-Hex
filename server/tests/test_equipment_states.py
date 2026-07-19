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
    """The combat pipeline resolves weapon_profile(player.weapon_id) with no
    special case, so the default state must supply every field combat reads,
    the same shape a rolled item base does."""
    entry = store.weapons[store.default_equipment_state]
    for field in ("damage_min", "damage_max", "speed_ticks", "type"):
        assert field in entry, f"default state cannot supply {field} to combat"
    assert entry["type"] in store.weapon_classes


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


def test_unarmed_is_a_weak_weapon_not_a_special_case(store):
    """Confirmed spec: unarmed is a 0%-20% max, speed-1 weapon like any other
    -- see test_weapon_power.py for how that multiplier is actually applied."""
    entry = store.weapons[store.default_equipment_state]
    assert (entry["damage_min"], entry["damage_max"], entry["speed_ticks"]) == (0.0, 0.20, 1)
    assert entry["type"] in store.weapon_classes


def test_weapon_fields_are_present_on_every_equipment_entry(store):
    """The new schema's fields must actually be there, not silently defaulted
    -- a missing one would surface as a config load error, not a weak weapon."""
    for item_id, data in store.weapons.items():
        for field in ("damage_min", "damage_max", "speed_ticks", "type"):
            assert field in data, f"{item_id} missing {field}"


def test_loader_rejects_a_non_numeric_damage_min(tmp_path):
    import json
    import shutil
    from gep.config_loader import ConfigError
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    entry = tmp_path / "config" / "weapons" / "equipment_handler.json"
    data = json.loads(entry.read_text())
    data["damage_min"] = "hard"
    entry.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="damage_min"):
        ConfigStore(tmp_path / "config")


def test_loader_rejects_a_zero_speed(tmp_path):
    """Zero would swing every tick regardless of the equipment -- the pacing
    gate failing open, not a very fast weapon."""
    import json
    import shutil
    from gep.config_loader import ConfigError
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    entry = tmp_path / "config" / "weapons" / "equipment_handler.json"
    data = json.loads(entry.read_text())
    data["speed_ticks"] = 0
    entry.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="speed_ticks"):
        ConfigStore(tmp_path / "config")
