import pathlib

import pytest

from gep.config_loader import ConfigError, ConfigStore

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


def test_loads_real_config_dir():
    store = ConfigStore(CONFIG_DIR)
    assert "cave_rat" in store.monsters
    assert "goblin_skirmisher" in store.monsters
    assert "iron_ore" in store.resources
    assert "copper_ore" in store.resources
    assert "fists" in store.weapons
    assert len(store.skills["non_combat_skills"]) == 8
    assert isinstance(store.xp_table, dict)
    assert str(1) in store.xp_table
    assert str(2000) in store.xp_table
    assert isinstance(store.modifiers, list)
    assert len(store.modifiers) == 54


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


def test_rejects_dangling_ruleset_reference(tmp_path):
    _mirror_config(tmp_path)
    ruleset_path = tmp_path / "floor_ruleset.json"
    ruleset_path.write_text(
        ruleset_path.read_text(encoding="utf-8").replace("cave_rat", "nonexistent_monster"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="unknown monster"):
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
        "loot_table": [["nothing", 1]]
    }""", encoding="utf-8")
    with pytest.raises(ConfigError, match="minrandom > maxrandom"):
        ConfigStore(tmp_path)


def _mirror_config(tmp_path: pathlib.Path) -> None:
    import shutil
    shutil.copytree(CONFIG_DIR, tmp_path, dirs_exist_ok=True)
