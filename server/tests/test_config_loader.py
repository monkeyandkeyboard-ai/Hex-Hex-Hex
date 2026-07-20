import json
import pathlib

import pytest

from gep.config_loader import ITEM_STATS, ConfigError, ConfigStore
from gep.items import MAX_TIER, MIN_TIER

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


def test_loads_real_config_dir():
    store = ConfigStore(CONFIG_DIR)
    assert "cave_rat" in store.monsters
    assert "goblin_skirmisher" in store.monsters
    assert "iron_ore" in store.resources
    assert "copper_ore" in store.resources
    assert "unarmed" in store.weapons
    assert len(store.skills["non_combat_skills"]) == 8
    assert isinstance(store.xp_table, dict)
    assert str(1) in store.xp_table
    assert str(2000) in store.xp_table
    assert isinstance(store.modifiers, list)
    # Every rollable stat must be present at all nine tiers -- a stat that
    # stops partway up would silently become impossible to roll on high-tier
    # gear, which no other assertion would catch.
    stats = {m["stat"] for m in store.modifiers}
    assert stats == set(ITEM_STATS)
    for stat in stats:
        tiers = sorted(m["tier"] for m in store.modifiers if m["stat"] == stat)
        assert tiers == list(range(MIN_TIER, MAX_TIER + 1)), stat


def test_real_stat_scaling_values(store=None):
    store = ConfigStore(CONFIG_DIR)
    ss = store.stat_scaling
    assert ss["hp_base"] == 500
    assert ss["hp_per_con"] == 10


def test_rejects_missing_required_key(tmp_path):
    _mirror_config(tmp_path)
    bad_monster = tmp_path / "monsters" / "broken.json"
    bad_monster.write_text('{"id": "broken", "display_name": "Broken"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="missing required keys"):
        ConfigStore(tmp_path)


def test_rejects_id_filename_mismatch(tmp_path):
    _mirror_config(tmp_path)
    mismatched = tmp_path / "resources" / "wrong_name.json"
    mismatched.write_text(
        (CONFIG_DIR / "resources" / "iron_ore.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="must match filename"):
        ConfigStore(tmp_path)


def test_rejects_dangling_biome_reference(tmp_path):
    _mirror_config(tmp_path)
    biome_path = tmp_path / "biomes" / "rocky.json"
    biome_path.write_text(
        biome_path.read_text(encoding="utf-8").replace("cave_rat", "nonexistent_monster"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown monster"):
        ConfigStore(tmp_path)


def test_rejects_dangling_archetype_biome(tmp_path):
    _mirror_config(tmp_path)
    arch_path = tmp_path / "floor_archetypes.json"
    arch_path.write_text(
        arch_path.read_text(encoding="utf-8").replace('"rocky"', '"nonexistent_biome"'),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown biome"):
        ConfigStore(tmp_path)


def test_rejects_minrandom_greater_than_maxrandom(tmp_path):
    _mirror_config(tmp_path)
    bad = tmp_path / "monsters" / "bad_variance.json"
    bad.write_text("""{
        "id": "bad_variance", "display_name": "Bad", "level": 1,
        "rarity_min": 1, "rarity_max": 5,
        "skills": {
            "constitution":    {"base": 5, "minrandom": 3, "maxrandom": 1},
            "precision":       {"base": 5, "minrandom": 0, "maxrandom": 0},
            "strength":        {"base": 5, "minrandom": 0, "maxrandom": 0},
            "dexterity":       {"base": 5, "minrandom": 0, "maxrandom": 0},
            "arcana":          {"base": 0, "minrandom": 0, "maxrandom": 0},
            "mana_attunement": {"base": 0, "minrandom": 0, "maxrandom": 0}
        },
        "combat": {"damage_min": 3, "damage_max": 6, "speed_ticks": 4},
        "respawn_ticks": 30, "xp_reward": {"combat_base": 10},
        "reward_table": "EMPTY"
    }""", encoding="utf-8")
    with pytest.raises(ConfigError, match="minrandom > maxrandom"):
        ConfigStore(tmp_path)


def _mirror_config(tmp_path: pathlib.Path) -> None:
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path, dirs_exist_ok=True)


def test_default_equipment_state_resolves_to_a_real_registry_entry():
    """"Nothing equipped" is an id, not an absence -- and the id has to exist.
    Validated at load so a typo cannot surface as an unarmed player who
    silently deals no damage."""
    store = ConfigStore(CONFIG_DIR)
    assert store.default_equipment_state == "unarmed"
    assert store.default_equipment_state in store.weapons


def test_unknown_default_equipment_state_is_rejected(tmp_path):
    import json
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    world = tmp_path / "config" / "world.json"
    data = json.loads(world.read_text())
    data["default_equipment_state"] = "nonexistent_state"
    world.write_text(json.dumps(data))

    with pytest.raises(ConfigError, match="not in config/weapons"):
        ConfigStore(tmp_path / "config")


def test_missing_default_equipment_state_is_rejected(tmp_path):
    import json
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    world = tmp_path / "config" / "world.json"
    data = json.loads(world.read_text())
    del data["default_equipment_state"]
    world.write_text(json.dumps(data))

    with pytest.raises(ConfigError, match="default_equipment_state"):
        ConfigStore(tmp_path / "config")


def test_every_equipment_entry_carries_the_full_schema():
    """The default state is not exempt from normalization -- that is the point
    of routing it through the registry rather than a fallback branch."""
    store = ConfigStore(CONFIG_DIR)
    for item_id, data in store.weapons.items():
        assert isinstance(data["equip_requirements"], dict), item_id
        for field in ("damage_min", "damage_max", "speed_ticks", "type", "equipment_slot"):
            assert field in data, f"{item_id} missing {field}"


def test_default_state_resolves_to_a_real_weapon_class():
    """damage_type is derived from the weapon's type via weapon_classes.json
    and power_scaling.json, not stored on the entry itself -- one source of
    truth for what a class deals, same as a rolled item base."""
    store = ConfigStore(CONFIG_DIR)
    weapon_class = store.weapon_classes[store.weapons["unarmed"]["type"]]
    damage_type = store.power_scaling[weapon_class]["damage_type"]
    assert damage_type in store.combat_constants["damage_type_weighting"]


def test_bad_equip_requirements_shape_is_rejected(tmp_path):
    import json
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    entry = tmp_path / "config" / "weapons" / "equipment_handler.json"
    data = json.loads(entry.read_text())
    data["equip_requirements"] = {"strength": "not a number"}
    entry.write_text(json.dumps(data))

    with pytest.raises(ConfigError, match="must be a number"):
        ConfigStore(tmp_path / "config")


def test_duplicate_equipment_ids_are_caught_without_the_filename_rule(tmp_path):
    """Equipment filenames no longer have to match ids, so the filename rule
    can't catch a copy-pasted entry. The explicit duplicate check must."""
    import json
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    weapons = tmp_path / "config" / "weapons"
    clone = json.loads((weapons / "equipment_handler.json").read_text())
    (weapons / "some_other_file.json").write_text(json.dumps(clone))

    with pytest.raises(ConfigError, match="duplicate id"):
        ConfigStore(tmp_path / "config")


# --- Gathering categories -------------------------------------------------

def test_loads_resource_categories():
    store = ConfigStore(CONFIG_DIR)
    assert set(store.resource_categories) - {"_comment"} == {"mineral", "herb", "tree"}
    assert store.resource_categories["herb"]["skill"] == "foraging"
    assert store.resources["glowcap_moss"]["category"] == "herb"
    assert store.resources["fungal_stalk"]["category"] == "tree"


def test_rejects_unknown_resource_category(tmp_path):
    _mirror_config(tmp_path)
    node = tmp_path / "resources" / "iron_ore.json"
    data = json.loads(node.read_text(encoding="utf-8"))
    data["category"] = "gemstone"
    node.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown category"):
        ConfigStore(tmp_path)


def test_rejects_resource_whose_skill_contradicts_its_category(tmp_path):
    """The failure this guards is silent in play: a herb that awards mineralogy
    still gathers, still looks like a herb, and trains the wrong profession."""
    _mirror_config(tmp_path)
    node = tmp_path / "resources" / "glowcap_moss.json"
    data = json.loads(node.read_text(encoding="utf-8"))
    data["skill"] = "mineralogy"
    node.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="harvested with 'foraging'"):
        ConfigStore(tmp_path)


def test_rejects_resource_that_never_trains_its_own_skill(tmp_path):
    _mirror_config(tmp_path)
    node = tmp_path / "resources" / "charred_snag.json"
    data = json.loads(node.read_text(encoding="utf-8"))
    data["xp"] = {"strength": 5}
    node.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="awards no 'aboriculture' XP"):
        ConfigStore(tmp_path)


def test_rejects_category_naming_a_skill_that_does_not_exist(tmp_path):
    _mirror_config(tmp_path)
    path = tmp_path / "resource_categories.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["herb"]["skill"] = "herbalism"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown gathering skill"):
        ConfigStore(tmp_path)
