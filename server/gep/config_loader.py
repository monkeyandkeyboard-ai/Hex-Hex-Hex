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
    "base_power",
    "cooldown_ticks",
    "equipment_slot",
}

_BIOME_REQUIRED = {
    "id",
    "display_name",
    "color",
    "resource_spawn_chance",
    "resource_weights",
    "monster_weight",
    "monster_weights",
}


class ConfigError(Exception):
    pass


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _load_dir(
    path: Path,
    required_keys: set[str],
    id_matches_filename: bool = True,
) -> dict[str, dict]:
    """Load every .json in a content directory, keyed by its `id` field.

    `id_matches_filename` is on everywhere except equipment, where filenames
    are organizational rather than identifying. It is worth keeping on by
    default: it catches a file copied from another and never re-`id`'d, which
    would otherwise silently overwrite the entry it was copied from. Where it
    is off, a duplicate id is caught explicitly below instead.
    """
    entries: dict[str, dict] = {}
    if not path.is_dir():
        raise ConfigError(f"missing config directory: {path}")
    for file in sorted(path.glob("*.json")):
        data = _load_json(file)
        missing = required_keys - data.keys()
        if missing:
            raise ConfigError(f"{file}: missing required keys {sorted(missing)}")
        if id_matches_filename and data["id"] != file.stem:
            raise ConfigError(
                f"{file}: 'id' field ({data['id']!r}) must match filename ({file.stem!r})"
            )
        if data["id"] in entries:
            raise ConfigError(f"{file}: duplicate id {data['id']!r}")
        entries[data["id"]] = data
    return entries


MONSTER_VISUAL_DEFAULTS: dict = {
    "sprite": "monster_placeholder",
    "hue_rotate": 0.0,   # degrees, 0-360
    "saturate": 1.0,     # multiplier
    "brightness": 1.0,   # multiplier
    "scale": 1.0,        # tile-relative size multiplier
    "tint": "#3a1010",   # tile fill behind the sprite
}


MONSTER_MOVEMENT_DEFAULTS: dict = {
    "wander_interval_ticks": 6,   # ticks between wander attempts
    "wander_chance": 0.5,         # probability of stepping on each attempt
    "pursue_interval_ticks": 2,   # ticks between steps while hunting a player
}


def _normalize_monster_movement(monsters: dict[str, dict]) -> None:
    """Same contract as visuals: fill omitted keys, reject nonsense early."""
    for monster_id, data in monsters.items():
        move = {**MONSTER_MOVEMENT_DEFAULTS, **data.get("movement", {})}
        unknown = move.keys() - MONSTER_MOVEMENT_DEFAULTS.keys()
        if unknown:
            raise ConfigError(f"monster {monster_id}: unknown movement keys {sorted(unknown)}")
        for field in ("wander_interval_ticks", "pursue_interval_ticks"):
            if not isinstance(move[field], int) or move[field] < 1:
                raise ConfigError(f"monster {monster_id}: {field!r} must be an integer >= 1")
        if not 0 <= move["wander_chance"] <= 1:
            raise ConfigError(f"monster {monster_id}: 'wander_chance' must be within 0..1")
        data["movement"] = move


def _normalize_monster_visuals(monsters: dict[str, dict]) -> None:
    """Fill in any omitted visual keys so every template serializes a complete
    block. Content uses one placeholder sprite; only these modifiers vary.
    """
    for monster_id, data in monsters.items():
        visual = {**MONSTER_VISUAL_DEFAULTS, **data.get("visual", {})}
        unknown = visual.keys() - MONSTER_VISUAL_DEFAULTS.keys()
        if unknown:
            raise ConfigError(f"monster {monster_id}: unknown visual keys {sorted(unknown)}")
        for field in ("hue_rotate", "saturate", "brightness", "scale"):
            if not isinstance(visual[field], (int, float)):
                raise ConfigError(f"monster {monster_id}: visual '{field}' must be numeric")
        if visual["scale"] <= 0:
            raise ConfigError(f"monster {monster_id}: visual 'scale' must be > 0")
        data["visual"] = visual


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
        # How close the monster must be to swing. 1 = adjacent (melee).
        # Monsters can never share a tile with a player -- is_passable forbids
        # it and pursuit halts at adjacent -- so a range of 0 would mean the
        # monster closes the gap and then never attacks.
        combat.setdefault("attack_range_tiles", 1)
        if not isinstance(combat["attack_range_tiles"], int) or combat["attack_range_tiles"] < 1:
            raise ConfigError(
                f"monster {monster_id}: 'attack_range_tiles' must be an integer >= 1"
            )


def _normalize_equipment(weapons: dict) -> None:
    """Fill in the optional half of the equipment schema.

    Every entry in the registry gets the same treatment -- the default state
    is not exempt. That is the point: requirement checks and damage typing
    must run down one path, so a future `equip_requirements` gate cannot be
    bypassed by a state that skipped normalization.
    """
    for item_id, data in weapons.items():
        # base_power is a flat baseline, not a range: per-swing variance is
        # deliberately gone, so damage is decided by stat multipliers and
        # mitigation alone.
        if not isinstance(data["base_power"], (int, float)) or isinstance(data["base_power"], bool):
            raise ConfigError(f"equipment {item_id}: 'base_power' must be a number")
        if data["base_power"] < 0:
            raise ConfigError(f"equipment {item_id}: 'base_power' must be >= 0")

        # Zero would mean a swing every tick regardless of the weapon, which
        # is the pacing gate failing open rather than a very fast weapon.
        if not isinstance(data["cooldown_ticks"], int) or isinstance(data["cooldown_ticks"], bool):
            raise ConfigError(f"equipment {item_id}: 'cooldown_ticks' must be an integer")
        if data["cooldown_ticks"] < 1:
            raise ConfigError(f"equipment {item_id}: 'cooldown_ticks' must be >= 1")

        data.setdefault("damage_type", "physical")
        if not isinstance(data["damage_type"], str):
            raise ConfigError(f"equipment {item_id}: 'damage_type' must be a string")

        # Empty today. Declared now so the shape is fixed before anything
        # depends on it, and so an entry that omits it is indistinguishable
        # from one that requires nothing.
        data.setdefault("equip_requirements", {})
        if not isinstance(data["equip_requirements"], dict):
            raise ConfigError(
                f"equipment {item_id}: 'equip_requirements' must be an object"
            )
        for skill, minimum in data["equip_requirements"].items():
            if not isinstance(minimum, (int, float)):
                raise ConfigError(
                    f"equipment {item_id}: requirement {skill!r} must be a number"
                )


class ConfigStore:
    def __init__(self, config_dir: str | Path):
        root = Path(config_dir)

        self.world = _load_json(root / "world.json")
        self.floor_ruleset = _load_json(root / "floor_ruleset.json")
        self.floor_archetypes = _load_json(root / "floor_archetypes.json")
        self.skills = _load_json(root / "skills.json")
        self.combat_constants = _load_json(root / "combat_scaling_constants.json")
        self.xp_rates = _load_json(root / "xp_rates.json")
        self.stat_scaling = _load_json(root / "stat_scaling.json")
        self.xp_table = _load_json(root / "xp_table.json")
        self.modifiers = _load_json(root / "modifiers.json")

        self.monsters = _load_dir(root / "monsters", _MONSTER_REQUIRED)
        _validate_monster_stats(self.monsters)
        _normalize_monster_visuals(self.monsters)
        _normalize_monster_movement(self.monsters)

        self.resources = _load_dir(root / "resources", _RESOURCE_REQUIRED)
        # Equipment filenames are organizational, not identifying: the entry
        # id is what the registry and every reference use.
        self.weapons = _load_dir(root / "weapons", _WEAPON_REQUIRED,
                                 id_matches_filename=False)
        _normalize_equipment(self.weapons)
        self.default_equipment_state = self._resolve_default_equipment_state()

        self.biomes = _load_dir(root / "biomes", _BIOME_REQUIRED)
        self._validate_biomes()
        self._validate_archetypes()
        self._validate_loot_tables()

    def _resolve_default_equipment_state(self) -> str:
        """The equipment id a player holds when main_hand is empty.

        Validated against the registry at load, so "nothing equipped" can
        never resolve to a state that does not exist. A missing or unknown
        value is a hard error rather than a silent fall back to a built-in
        default -- that fall back is exactly the special case this replaces.
        """
        state = self.world.get("default_equipment_state")
        if not state:
            raise ConfigError("world.json: 'default_equipment_state' is required")
        if state not in self.weapons:
            raise ConfigError(
                f"world.json: default_equipment_state {state!r} is not in config/weapons/"
            )
        return state

    def _validate_loot_tables(self) -> None:
        """Loot entries must name something that exists. Runs after resources
        and weapons are loaded, since a drop may be either. Without this a
        typo'd item id stays silent until the moment something dies.
        """
        from gep.loot import NOTHING

        for monster_id, data in self.monsters.items():
            for pair in data.get("loot_table") or []:
                item_id, weight = pair[0], pair[1]
                if item_id != NOTHING and item_id not in self.resources and item_id not in self.weapons:
                    raise ConfigError(f"monster {monster_id}: unknown loot item {item_id!r}")
                if weight < 0:
                    raise ConfigError(f"monster {monster_id}: negative loot weight for {item_id!r}")
            rolls = data.get("loot_rolls", 1)
            if not isinstance(rolls, int) or rolls < 0:
                raise ConfigError(f"monster {monster_id}: 'loot_rolls' must be an integer >= 0")

    def _validate_biomes(self) -> None:
        for biome_id, data in self.biomes.items():
            for pair in data["resource_weights"]:
                if pair[0] not in self.resources:
                    raise ConfigError(f"biome {biome_id}: unknown resource {pair[0]!r}")
            for pair in data["monster_weights"]:
                if pair[0] not in self.monsters:
                    raise ConfigError(f"biome {biome_id}: unknown monster {pair[0]!r}")

    def _validate_archetypes(self) -> None:
        archetypes = self.floor_archetypes.get("archetypes", {})
        for name, params in archetypes.items():
            for biome_id in self._archetype_biome_refs(params):
                if biome_id not in self.biomes:
                    raise ConfigError(f"archetype {name}: unknown biome {biome_id!r}")
            layout = params.get("layout", {})
            mode = layout.get("mode")
            if mode not in ("radial", "elevation", "cluster"):
                raise ConfigError(f"archetype {name}: unknown layout mode {mode!r}")
            if mode == "radial" and not layout.get("bands"):
                raise ConfigError(f"archetype {name}: radial layout needs 'bands'")
            if mode == "elevation" and not layout.get("strata"):
                raise ConfigError(f"archetype {name}: elevation layout needs 'strata'")
            if mode == "cluster" and not layout.get("biome_weights"):
                raise ConfigError(f"archetype {name}: cluster layout needs 'biome_weights'")
        for rule in self.floor_archetypes.get("overrides", []):
            if rule["archetype"] not in archetypes:
                raise ConfigError(f"archetype override references unknown archetype {rule['archetype']!r}")
        default = self.floor_archetypes.get("default_archetype")
        if default not in archetypes:
            raise ConfigError(f"default_archetype {default!r} is not defined")

    @staticmethod
    def _archetype_biome_refs(params: dict) -> list[str]:
        """Every biome id an archetype template can reference, across all
        layout modes plus the fallback/forbid rules."""
        refs: list[str] = []
        layout = params.get("layout", {})
        refs += [b["biome"] for b in layout.get("bands", [])]
        refs += [b["biome"] for b in layout.get("strata", [])]
        refs += [pair[0] for pair in layout.get("biome_weights", [])]
        refs += list(layout.get("radial_constraints", {}).keys())
        extremity = layout.get("extremity")
        if extremity:
            refs.append(extremity["biome"])
        refs += params.get("forbid_biomes", [])
        if params.get("fallback_biome"):
            refs.append(params["fallback_biome"])
        return refs
