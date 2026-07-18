"""Loads every JSON config file at worker startup (compendium §19). Engine
code reads through this object and never special-cases an entity by
name/ID -- any file of the right shape just works. Fails loudly at startup
on a malformed file rather than partway through a tick.
"""
import json
from pathlib import Path

COMBAT_SKILLS = (
    "precision",
    "strength",
    "dexterity",
    "arcana",
    "mana_attunement",
    "constitution",
)

_MONSTER_REQUIRED = {
    "id",
    "display_name",
    "level",
    "rarity_min",
    "rarity_max",
    "skills",
    "combat",
    "respawn_ticks",
    "xp_reward",
    "loot_table",
}

_RESOURCE_REQUIRED = {
    "id",
    "display_name",
    "skill",
    "gather_ticks",
    "respawn_ticks",
    "xp",
    "yield",
    "grade_weights",
}

_WEAPON_REQUIRED = {
    "id",
    "display_name",
    "damage_min",
    "damage_max",
    "speed_ticks",
    "equipment_slot",
}


class ConfigError(Exception):
    pass


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_dir(path: Path, required_keys: set[str]) -> dict[str, dict]:
    entries: dict[str, dict] = {}
    if not path.is_dir():
        raise ConfigError(f"missing config directory: {path}")
    for file in sorted(path.glob("*.json")):
        data = _load_json(file)
        missing = required_keys - data.keys()
        if missing:
            raise ConfigError(f"{file}: missing required keys {sorted(missing)}")
        if data["id"] != file.stem:
            raise ConfigError(
                f"{file}: 'id' field ({data['id']!r}) must match filename ({file.stem!r})"
            )
        entries[data["id"]] = data
    return entries


def _validate_monster_stats(monsters: dict[str, dict]) -> None:
    for monster_id, data in monsters.items():
        missing_skills = set(COMBAT_SKILLS) - data["skills"].keys()
        if missing_skills:
            raise ConfigError(f"monster {monster_id}: missing skill blocks {sorted(missing_skills)}")
        for skill, block in data["skills"].items():
            for field in ("base", "minrandom", "maxrandom"):
                if field not in block:
                    raise ConfigError(f"monster {monster_id}: skill {skill} missing '{field}'")
            if block["minrandom"] > block["maxrandom"]:
                raise ConfigError(f"monster {monster_id}: skill {skill} minrandom > maxrandom")
        combat = data["combat"]
        for field in ("damage_min", "damage_max", "speed_ticks"):
            if field not in combat:
                raise ConfigError(f"monster {monster_id}: combat block missing '{field}'")
        if combat["damage_min"] > combat["damage_max"]:
            raise ConfigError(f"monster {monster_id}: damage_min > damage_max")


class ConfigStore:
    def __init__(self, config_dir: str | Path):
        root = Path(config_dir)

        self.world = _load_json(root / "world.json")
        self.floor_ruleset = _load_json(root / "floor_ruleset.json")
        self.skills = _load_json(root / "skills.json")
        self.combat_constants = _load_json(root / "combat_scaling_constants.json")
        self.xp_rates = _load_json(root / "xp_rates.json")
        self.stat_scaling = _load_json(root / "stat_scaling.json")
        self.xp_table = _load_json(root / "xp_table.json")
        self.modifiers = _load_json(root / "modifiers.json")

        self.monsters = _load_dir(root / "monsters", _MONSTER_REQUIRED)
        _validate_monster_stats(self.monsters)

        self.resources = _load_dir(root / "resources", _RESOURCE_REQUIRED)
        self.weapons = _load_dir(root / "weapons", _WEAPON_REQUIRED)

        for weight_pair in self.floor_ruleset["monster_weights"]:
            if weight_pair[0] not in self.monsters:
                raise ConfigError(f"floor_ruleset references unknown monster {weight_pair[0]!r}")
        for weight_pair in self.floor_ruleset["resource_weights"]:
            if weight_pair[0] not in self.resources:
                raise ConfigError(f"floor_ruleset references unknown resource {weight_pair[0]!r}")
