"""Loads every JSON config file at worker startup (compendium §19). Engine
code reads through this object and never special-cases an entity by
name/ID -- any file of the right shape just works. Fails loudly at startup
on a malformed file rather than partway through a tick.
"""
import json
from pathlib import Path

from gep.items import BASE_REQUIRED_KEYS, MAX_TIER, MIN_TIER, PERCENT_SUFFIX, ItemRegistry
from gep.rewards import RewardService

COMBAT_SKILLS = (
    "precision",
    "strength",
    "dexterity",
    "arcana",
    "mana_attunement",
    "constitution",
)

# Stats an item base or modifier may grant. Combat skills plus the derived
# stats that are not skills in their own right. Every implicit and every
# modifier is checked against this at load, so a typo'd stat name is a startup
# failure rather than an item that silently grants nothing.
ITEM_STATS = frozenset(COMBAT_SKILLS) | {"critical_strike_chance"}

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
    "reward_table",
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
    "equipment_slot",
    "type",
    "damage_min",
    "damage_max",
    "speed_ticks",
}

_BIOME_REQUIRED = {
    "id",
    "display_name",
    "color",
    "resource_spawn_chance",
    "resource_weights",
    "monster_weight",
    "monster_weights",
    # Whether the player can stand on this biome's tiles. Required rather than
    # defaulted to True: a biome that omits it is a biome whose author never
    # decided, and "water you can walk across" is the kind of wrong that looks
    # like a rendering bug for a week before anyone reads the config.
    "passable",
}

_PREFAB_REQUIRED = {
    "id",
    "footprint",
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


def _normalize_equipment(weapons: dict, weapon_classes: dict) -> None:
    """Fill in the optional half of the equipment schema.

    Every entry in the registry gets the same treatment -- the default state
    is not exempt. That is the point: requirement checks and damage typing
    must run down one path, so a future `equip_requirements` gate cannot be
    bypassed by a state that skipped normalization.

    Schema mirrors the item bases' weapon fields (damage_min/max as a
    multiplier on power, speed_ticks as the swing interval) so combat reads
    a rolled sword and the unarmed state through the same shape.
    """
    for item_id, data in weapons.items():
        for field in ("damage_min", "damage_max"):
            if not isinstance(data[field], (int, float)) or isinstance(data[field], bool):
                raise ConfigError(f"equipment {item_id}: {field!r} must be a number")
            if data[field] < 0:
                raise ConfigError(f"equipment {item_id}: {field!r} must be >= 0")
        if data["damage_min"] > data["damage_max"]:
            raise ConfigError(f"equipment {item_id}: damage_min > damage_max")

        # Zero would mean a swing every tick regardless of the weapon, which
        # is the pacing gate failing open rather than a very fast weapon.
        if not isinstance(data["speed_ticks"], int) or isinstance(data["speed_ticks"], bool):
            raise ConfigError(f"equipment {item_id}: 'speed_ticks' must be an integer")
        if data["speed_ticks"] < 1:
            raise ConfigError(f"equipment {item_id}: 'speed_ticks' must be >= 1")

        if data.get("type") not in weapon_classes:
            raise ConfigError(
                f"equipment {item_id}: type {data.get('type')!r} is not in weapon_classes.json"
            )

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


def _validate_weapon_classes(weapon_classes: dict) -> None:
    if not isinstance(weapon_classes, dict) or not weapon_classes:
        raise ConfigError("weapon_classes.json: must be a non-empty object")
    for weapon_type, weapon_class in weapon_classes.items():
        if weapon_type.startswith("_"):
            continue  # documentation keys
        if not isinstance(weapon_class, str) or not weapon_class:
            raise ConfigError(
                f"weapon_classes.json: {weapon_type!r} must map to a non-empty string"
            )


def _validate_power_scaling(power_scaling: dict, weapon_classes: dict, combat_constants: dict) -> None:
    """Every class a weapon type points at must resolve, and its damage_type
    must be one the mitigation pipeline already knows how to weight."""
    referenced = {c for t, c in weapon_classes.items() if not t.startswith("_")}
    missing = referenced - power_scaling.keys()
    if missing:
        raise ConfigError(
            f"power_scaling.json: missing classes referenced by weapon_classes.json: {sorted(missing)}"
        )
    weighting = combat_constants.get("damage_type_weighting", {})
    for weapon_class, data in power_scaling.items():
        if weapon_class.startswith("_"):
            continue  # documentation keys
        if not isinstance(data, dict):
            raise ConfigError(f"power_scaling.json: {weapon_class!r} must be an object")

        damage_type = data.get("damage_type")
        if damage_type not in weighting:
            raise ConfigError(
                f"power_scaling.json: {weapon_class!r} damage_type {damage_type!r} "
                f"is not in damage_type_weighting"
            )

        stats = data.get("stats")
        if not isinstance(stats, dict) or not stats:
            raise ConfigError(f"power_scaling.json: {weapon_class!r} 'stats' must be a non-empty object")
        for stat, coeff in stats.items():
            if stat not in COMBAT_SKILLS:
                raise ConfigError(f"power_scaling.json: {weapon_class!r} unknown stat {stat!r}")
            if not isinstance(coeff, (int, float)) or isinstance(coeff, bool) or coeff < 0:
                raise ConfigError(
                    f"power_scaling.json: {weapon_class!r} stat {stat!r} coefficient must be a number >= 0"
                )


def _validate_item_names(item_names: dict) -> None:
    for key in ("adjectives", "nouns"):
        words = item_names.get(key)
        if not isinstance(words, list) or not words:
            raise ConfigError(f"item_names.json: {key!r} must be a non-empty list")
        for word in words:
            if not isinstance(word, str) or not word:
                raise ConfigError(f"item_names.json: {key!r} entries must be non-empty strings")


def _validate_weapon_types(bases: dict, weapon_classes: dict) -> None:
    """Every main_hand/two_hand base's archetype must resolve to a combat
    class -- those are the only bases that can end up as a player's
    weapon_id, so an unmapped type would only surface the moment one dropped
    and got equipped."""
    for code, base in bases.items():
        if base["equipment_slot"] in ("main_hand", "two_hand") and base["type"] not in weapon_classes:
            raise ConfigError(
                f"item base {code}: type {base['type']!r} is not in weapon_classes.json"
            )


def _load_item_bases(path: Path) -> dict[str, dict]:
    """Merge every *_bases.json into one registry keyed by base code.

    The split into per-slot files is organizational only -- codes are unique
    across the whole item space, and a collision between two files is a hard
    error rather than one file quietly winning.
    """
    if not path.is_dir():
        raise ConfigError(f"missing config directory: {path}")

    bases: dict[str, dict] = {}
    origin: dict[str, str] = {}
    for file in sorted(path.glob("*_bases.json")):
        data = _load_json(file)
        for code, entry in data.items():
            if code.startswith("_"):
                continue  # documentation keys
            if code in bases:
                raise ConfigError(
                    f"{file.name}: duplicate item base code {code!r} "
                    f"(already defined in {origin[code]})"
                )
            bases[code] = entry
            origin[code] = file.name
    if not bases:
        raise ConfigError(f"{path}: no item bases found")
    return bases


def _validate_item_bases(bases: dict[str, dict]) -> None:
    for code, base in bases.items():
        missing = BASE_REQUIRED_KEYS - base.keys()
        if missing:
            raise ConfigError(f"item base {code}: missing required keys {sorted(missing)}")

        tier = base["Tier"]
        if not isinstance(tier, int) or not MIN_TIER <= tier <= MAX_TIER:
            raise ConfigError(
                f"item base {code}: 'Tier' must be an integer {MIN_TIER}..{MAX_TIER}"
            )

        drop_tier = base["drop_tier"]
        if not isinstance(drop_tier, (int, float)) or drop_tier < 0:
            raise ConfigError(f"item base {code}: 'drop_tier' must be a number >= 0")

        for field in ("damage_min", "damage_max", "armor", "base_sell_value", "speed_ticks"):
            if not isinstance(base[field], (int, float)) or isinstance(base[field], bool):
                raise ConfigError(f"item base {code}: {field!r} must be a number")
            if base[field] < 0:
                raise ConfigError(f"item base {code}: {field!r} must be >= 0")

        if base["damage_min"] > base["damage_max"]:
            raise ConfigError(f"item base {code}: damage_min > damage_max")

        if not isinstance(base["max_stack"], int) or base["max_stack"] < 1:
            raise ConfigError(f"item base {code}: 'max_stack' must be an integer >= 1")

        implicits = base["implicits"]
        if not isinstance(implicits, dict):
            raise ConfigError(f"item base {code}: 'implicits' must be an object")
        for stat, value in implicits.items():
            root_stat = stat[: -len(PERCENT_SUFFIX)] if stat.endswith(PERCENT_SUFFIX) else stat
            if root_stat not in ITEM_STATS:
                raise ConfigError(f"item base {code}: implicit names unknown stat {stat!r}")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"item base {code}: implicit {stat!r} must be a number")


def _validate_modifiers(modifiers: list) -> None:
    if not isinstance(modifiers, list) or not modifiers:
        raise ConfigError("modifiers.json: must be a non-empty list")

    seen: set[str] = set()
    for entry in modifiers:
        code = entry.get("modifier_code")
        if not isinstance(code, str) or len(code) != 3:
            raise ConfigError(f"modifier {code!r}: 'modifier_code' must be 3 characters")
        if code in seen:
            raise ConfigError(f"modifier {code!r}: duplicate modifier_code")
        seen.add(code)

        if entry.get("stat") not in ITEM_STATS:
            raise ConfigError(f"modifier {code}: unknown stat {entry.get('stat')!r}")

        tier = entry.get("tier")
        if not isinstance(tier, int) or not MIN_TIER <= tier <= MAX_TIER:
            raise ConfigError(f"modifier {code}: 'tier' must be an integer {MIN_TIER}..{MAX_TIER}")

        weight = entry.get("weight")
        if not isinstance(weight, (int, float)) or weight < 0:
            raise ConfigError(f"modifier {code}: 'weight' must be a number >= 0")

        low, high = entry.get("min_value"), entry.get("max_value")
        for field, value in (("min_value", low), ("max_value", high)):
            if not isinstance(value, int) or value < 0:
                raise ConfigError(f"modifier {code}: {field!r} must be an integer >= 0")
        if low > high:
            raise ConfigError(f"modifier {code}: min_value > max_value")


class ConfigStore:
    def __init__(self, config_dir: str | Path):
        root = Path(config_dir)

        self.world = _load_json(root / "world.json")
        self.floor_ruleset = _load_json(root / "floor_ruleset.json")
        self.floor_archetypes = _load_json(root / "floor_archetypes.json")
        self.spawn_ruleset = _load_json(root / "spawn_ruleset.json")
        self.skills = _load_json(root / "skills.json")
        self.combat_constants = _load_json(root / "combat_scaling_constants.json")
        self.xp_rates = _load_json(root / "xp_rates.json")
        self.stat_scaling = _load_json(root / "stat_scaling.json")
        self.xp_table = _load_json(root / "xp_table.json")
        self.modifiers = _load_json(root / "modifiers.json")

        self.weapon_classes = _load_json(root / "weapon_classes.json")
        self.power_scaling = _load_json(root / "power_scaling.json")
        _validate_weapon_classes(self.weapon_classes)
        _validate_power_scaling(self.power_scaling, self.weapon_classes, self.combat_constants)

        self.loot_tables = _load_json(root / "loot" / "tables.json")
        self.reward_profiles = _load_json(root / "loot" / "rewards.json")
        self.item_generation = _load_json(root / "item_generation.json")
        self.item_names = _load_json(root / "item_names.json")
        _validate_item_names(self.item_names)
        self.item_bases = _load_item_bases(root / "items")
        _validate_item_bases(self.item_bases)
        _validate_weapon_types(self.item_bases, self.weapon_classes)
        _validate_modifiers(self.modifiers)
        self.items = ItemRegistry(
            self.item_bases, self.modifiers, self.item_generation, ITEM_STATS, self.item_names
        )
        self._validate_item_generation()
        # One service, built once, handed to every payout site.
        self.rewards = RewardService(self.reward_profiles, self.loot_tables, self.items)

        self.monsters = _load_dir(root / "monsters", _MONSTER_REQUIRED)
        _validate_monster_stats(self.monsters)
        _normalize_monster_visuals(self.monsters)
        _normalize_monster_movement(self.monsters)

        self.resources = _load_dir(root / "resources", _RESOURCE_REQUIRED)
        # Equipment filenames are organizational, not identifying: the entry
        # id is what the registry and every reference use.
        self.weapons = _load_dir(root / "weapons", _WEAPON_REQUIRED,
                                 id_matches_filename=False)
        _normalize_equipment(self.weapons, self.weapon_classes)
        self.default_equipment_state = self._resolve_default_equipment_state()

        self.biomes = _load_dir(root / "biomes", _BIOME_REQUIRED)
        self._validate_biomes()

        prefabs_dir = root / "prefabs"
        self.prefabs = _load_dir(prefabs_dir, _PREFAB_REQUIRED) if prefabs_dir.is_dir() else {}
        self._validate_prefabs()

        self._validate_archetypes()
        self._validate_loot_tables()
        self._validate_spawn_ruleset()
        self._validate_floor_ruleset()

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

    def _validate_floor_ruleset(self) -> None:
        """The exit separation window must be satisfiable for every floor.

        Checked here rather than in the generator because an unsatisfiable
        window does not fail on floor 1 -- it fails on whichever floor first
        happens to draw an up exit with no legal partner, which could be floor
        200 in front of a player. A startup error is the whole point.
        """
        rs = self.floor_ruleset
        radius = rs.get("radius")
        if not isinstance(radius, int) or radius < 1:
            raise ConfigError("floor_ruleset.json: 'radius' must be an integer >= 1")

        islands = rs.get("min_island_tiles")
        if not isinstance(islands, int) or isinstance(islands, bool) or islands < 1:
            raise ConfigError(
                "floor_ruleset.json: 'min_island_tiles' must be an integer >= 1"
            )

        sep = rs.get("exit_separation")
        if not isinstance(sep, dict):
            raise ConfigError("floor_ruleset.json: 'exit_separation' is required")
        for key in ("min_moves", "max_diameter_pct"):
            if key not in sep:
                raise ConfigError(f"floor_ruleset.json: exit_separation.{key!r} is required")

        min_moves = sep["min_moves"]
        pct = sep["max_diameter_pct"]
        if not isinstance(min_moves, int) or min_moves < 1:
            raise ConfigError("floor_ruleset.json: exit_separation.min_moves must be an integer >= 1")
        if not isinstance(pct, (int, float)) or not 0 < pct <= 1:
            raise ConfigError(
                "floor_ruleset.json: exit_separation.max_diameter_pct must be in (0, 1]"
            )

        max_moves = int(pct * 2 * radius)
        if min_moves > max_moves:
            raise ConfigError(
                f"floor_ruleset.json: exit_separation is empty -- min_moves {min_moves} "
                f"exceeds max_diameter_pct {pct} of diameter {2 * radius} (= {max_moves})"
            )
        # Floor 1's entrance is the centre, so its separation is fixed at
        # exactly the radius and cannot be re-rolled to satisfy the window.
        if not min_moves <= radius <= max_moves:
            raise ConfigError(
                f"floor_ruleset.json: floor 1's entrance is the centre, so its exit "
                f"separation is always the radius ({radius}), which falls outside "
                f"the configured window [{min_moves}, {max_moves}]"
            )

    def _validate_item_generation(self) -> None:
        """Every tier must declare a slot count, and no tier may exceed the
        declared ceiling. A tier missing from the table would raise a KeyError
        at the moment an item of that tier dropped, which is the worst
        possible time to discover it."""
        gen = self.item_generation
        slots = gen.get("tier_mod_slots") or {}
        max_prefixes = gen.get("max_prefixes")
        max_suffixes = gen.get("max_suffixes")
        for field, value in (("max_prefixes", max_prefixes), ("max_suffixes", max_suffixes)):
            if not isinstance(value, int) or value < 0:
                raise ConfigError(f"item_generation.json: {field!r} must be an integer >= 0")

        for tier in range(MIN_TIER, MAX_TIER + 1):
            entry = slots.get(str(tier))
            if entry is None:
                raise ConfigError(f"item_generation.json: tier {tier} missing from 'tier_mod_slots'")
            for field, ceiling in (("prefixes", max_prefixes), ("suffixes", max_suffixes)):
                count = entry.get(field)
                if not isinstance(count, int) or count < 0:
                    raise ConfigError(
                        f"item_generation.json: tier {tier} {field!r} must be an integer >= 0"
                    )
                if count > ceiling:
                    raise ConfigError(
                        f"item_generation.json: tier {tier} {field!r} ({count}) exceeds max ({ceiling})"
                    )

        cap = gen.get("modifier_tier_cap", "item_tier")
        if cap not in ("item_tier", "unrestricted"):
            raise ConfigError(
                f"item_generation.json: 'modifier_tier_cap' must be 'item_tier' or "
                f"'unrestricted', got {cap!r}"
            )

    def _validate_loot_tables(self) -> None:
        """Loot tables must name things that exist, every reward profile must
        reference tables that exist, and every source must reference a profile
        that exists. Without this a typo'd id stays silent until the moment
        something pays out.
        """
        from gep.rewards import KIND_EQUIPMENT, KIND_ITEMS, NOTHING

        for table_id, table in self.loot_tables.items():
            if table_id.startswith("_") or not isinstance(table, dict):
                continue  # documentation keys
            kind = table.get("kind", KIND_ITEMS)
            if kind == KIND_EQUIPMENT:
                chance = table.get("drop_chance")
                if not isinstance(chance, (int, float)) or not 0 <= chance <= 1:
                    raise ConfigError(
                        f"loot table {table_id}: 'drop_chance' must be within 0..1"
                    )
                low = int(table.get("min_tier", MIN_TIER))
                high = int(table.get("max_tier", MAX_TIER))
                if not MIN_TIER <= low <= high <= MAX_TIER:
                    raise ConfigError(
                        f"loot table {table_id}: tier range {low}..{high} is outside "
                        f"{MIN_TIER}..{MAX_TIER}"
                    )
                if not self.items.drop_candidates(
                    slots=table.get("slots"), min_tier=low, max_tier=high,
                    types=table.get("types"),
                ):
                    raise ConfigError(
                        f"loot table {table_id}: filters match no item bases, so the "
                        f"table can never drop anything"
                    )
                continue

            for pair in table.get("entries") or []:
                item_id, weight = pair[0], pair[1]
                if item_id != NOTHING and item_id not in self.resources:
                    raise ConfigError(f"loot table {table_id}: unknown item {item_id!r}")
                if weight < 0:
                    raise ConfigError(f"loot table {table_id}: negative weight for {item_id!r}")
            for item_id, bounds in (table.get("quantities") or {}).items():
                if item_id not in self.resources:
                    raise ConfigError(f"loot table {table_id}: quantity for unknown item {item_id!r}")
                if len(bounds) != 2 or any(not isinstance(b, int) or b < 0 for b in bounds):
                    raise ConfigError(
                        f"loot table {table_id}: quantity for {item_id!r} must be two "
                        f"integers >= 0"
                    )

        for profile_id, profile in self.reward_profiles.items():
            if profile_id.startswith("_") or not isinstance(profile, dict):
                continue  # documentation keys
            slots = profile.get("slots")
            if not isinstance(slots, list):
                raise ConfigError(f"reward profile {profile_id}: 'slots' must be a list")
            for slot in slots:
                table_id = slot.get("table")
                if table_id not in self.loot_tables or str(table_id).startswith("_"):
                    raise ConfigError(
                        f"reward profile {profile_id}: slot references unknown loot "
                        f"table {table_id!r}"
                    )
                rolls = slot.get("rolls", 1)
                if not isinstance(rolls, int) or rolls < 0:
                    raise ConfigError(
                        f"reward profile {profile_id}: slot 'rolls' must be an integer >= 0"
                    )

        # Sources reference profiles by id. Monsters are simply the first
        # kind of source; a container registry validates through this same
        # loop when one exists.
        for monster_id, data in self.monsters.items():
            profile_id = data.get("reward_table")
            if profile_id not in self.reward_profiles or str(profile_id).startswith("_"):
                raise ConfigError(
                    f"monster {monster_id}: unknown reward_table {profile_id!r}"
                )

    def _validate_biomes(self) -> None:
        for biome_id, data in self.biomes.items():
            for pair in data["resource_weights"]:
                if pair[0] not in self.resources:
                    raise ConfigError(f"biome {biome_id}: unknown resource {pair[0]!r}")
            for pair in data["monster_weights"]:
                if pair[0] not in self.monsters:
                    raise ConfigError(f"biome {biome_id}: unknown monster {pair[0]!r}")
            if not isinstance(data["passable"], bool):
                raise ConfigError(f"biome {biome_id}: 'passable' must be true or false")
            if data["passable"] is False:
                # Nothing can reach these tiles to fight or gather on them, so
                # weights there are silently dead config. Better to reject the
                # contradiction than to load it and have someone wonder why
                # their lake never spawns the fish they weighted into it.
                for key in ("resource_weights", "monster_weights"):
                    if data[key]:
                        raise ConfigError(
                            f"biome {biome_id}: is impassable but declares {key!r}; "
                            f"nothing can reach those tiles to spawn on them"
                        )

    def _validate_spawn_ruleset(self) -> None:
        """Every archetype the spawner names must exist, and vice versa isn't
        required -- an archetype with no entry simply spawns nothing (the
        spawner's default), which is how safe/town floors stay unpopulated
        without a redundant zero entry.
        """
        counts = self.spawn_ruleset.get("archetype_monster_counts", {})
        archetypes = self.floor_archetypes.get("archetypes", {})
        for name, count in counts.items():
            if name not in archetypes:
                raise ConfigError(
                    f"spawn_ruleset.json: unknown archetype {name!r} in archetype_monster_counts"
                )
            if not isinstance(count, int) or count < 0:
                raise ConfigError(
                    f"spawn_ruleset.json: archetype_monster_counts[{name!r}] must be an integer >= 0"
                )
        factor = self.spawn_ruleset.get("road_danger_factor", 0.1)
        if not isinstance(factor, (int, float)) or factor < 0:
            raise ConfigError("spawn_ruleset.json: 'road_danger_factor' must be a number >= 0")

    def _validate_prefabs(self) -> None:
        for prefab_id, data in self.prefabs.items():
            footprint = data["footprint"]
            if not isinstance(footprint, list) or not footprint:
                raise ConfigError(f"prefab {prefab_id}: 'footprint' must be a non-empty list")
            offsets = set()
            for offset in footprint:
                key = tuple(offset)
                if key in offsets:
                    raise ConfigError(f"prefab {prefab_id}: duplicate footprint offset {offset!r}")
                offsets.add(key)

            tile_sprites = data.get("tile_sprites", {})
            for key in tile_sprites:
                offset = tuple(int(p) for p in key.split(","))
                if offset not in offsets:
                    raise ConfigError(
                        f"prefab {prefab_id}: tile_sprites key {key!r} is not in 'footprint'"
                    )

            placement = data.get("placement", {})
            required_biome = placement.get("required_biome")
            if required_biome is not None:
                refs = [required_biome] if isinstance(required_biome, str) else required_biome
                for biome_id in refs:
                    if biome_id not in self.biomes:
                        raise ConfigError(
                            f"prefab {prefab_id}: unknown required_biome {biome_id!r}"
                        )

            count = data.get("count", {"min": 1, "max": 1})
            if count["min"] > count["max"] or count["min"] < 0:
                raise ConfigError(f"prefab {prefab_id}: invalid 'count' range {count!r}")

            effects = data.get("effects")
            if effects:
                override = effects.get("reward_table_override")
                if override and override not in self.reward_profiles:
                    raise ConfigError(
                        f"prefab {prefab_id}: effects.reward_table_override "
                        f"references unknown reward profile {override!r}"
                    )

    def _validate_archetypes(self) -> None:
        archetypes = self.floor_archetypes.get("archetypes", {})
        for name, params in archetypes.items():
            for biome_id in self._archetype_biome_refs(params):
                if biome_id not in self.biomes:
                    raise ConfigError(f"archetype {name}: unknown biome {biome_id!r}")
            for prefab_id in params.get("prefabs", []):
                if prefab_id not in self.prefabs:
                    raise ConfigError(f"archetype {name}: unknown prefab {prefab_id!r}")
            self._validate_pipeline(name, params)
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
            self._validate_generation_params(name, params)
        for rule in self.floor_archetypes.get("overrides", []):
            if rule["archetype"] not in archetypes:
                raise ConfigError(f"archetype override references unknown archetype {rule['archetype']!r}")
        default = self.floor_archetypes.get("default_archetype")
        if default not in archetypes:
            raise ConfigError(f"default_archetype {default!r} is not defined")

    @staticmethod
    def _validate_generation_params(name: str, params: dict) -> None:
        """Every number the generator reads must be declared here.

        These used to have defaults baked into gep/biome_layout.py and
        gep/pipeline.py. A default that every real archetype overrides is
        dead weight; a default that some archetype silently relies on is
        worse -- the floor gets generated from a value nobody chose, and the
        only symptom is that the terrain quietly changes shape when the
        default is later edited. Requiring them makes an omission a startup
        error instead.
        """
        for field in ("elevation", "roughness"):
            cfg = params.get(field)
            if not isinstance(cfg, dict):
                raise ConfigError(f"archetype {name}: '{field}' block is required")
            for key in ("octaves", "scale"):
                if cfg.get(key) is None:
                    raise ConfigError(f"archetype {name}: '{field}.{key}' is required")

        layout = params.get("layout", {})
        if layout.get("mode") == "cluster" and layout.get("region_count") is None:
            raise ConfigError(f"archetype {name}: cluster layout needs 'region_count'")

        extremity = layout.get("extremity")
        if extremity:
            for key in ("beyond_radius_pct", "elevation_above"):
                if extremity.get(key) is None:
                    raise ConfigError(f"archetype {name}: 'layout.extremity.{key}' is required")

        # Same rule as elevation/roughness: every number a feature reads is
        # declared, so a floor is never generated from a value nobody chose.
        for block, keys in (
            ("rivers", ("biome", "count", "meander", "width_chance")),
            ("chambers", ("wall_biome", "room_count", "room_radius_min",
                          "room_radius_max", "min_spacing", "inset_pct")),
        ):
            cfg = params.get(block)
            if cfg is None:
                continue
            for key in keys:
                if cfg.get(key) is None:
                    raise ConfigError(f"archetype {name}: '{block}.{key}' is required")
        rivers = params.get("rivers")
        if rivers and rivers["count"] < 1:
            raise ConfigError(f"archetype {name}: 'rivers.count' must be >= 1")
        chambers = params.get("chambers")
        if chambers and chambers["room_radius_min"] > chambers["room_radius_max"]:
            raise ConfigError(
                f"archetype {name}: 'chambers.room_radius_min' exceeds 'room_radius_max'"
            )

    @staticmethod
    def _validate_pipeline(name: str, params: dict) -> None:
        """An archetype's generation stack must name real, runnable stages.

        An unknown stage id would otherwise surface as a KeyError midway
        through generating a live floor. The terminal stages are rejected
        rather than ignored: a config that lists them reads as though it
        controls when they run, and it does not (gep/pipeline.py runs them
        last unconditionally) -- silently accepting the line would leave a
        false impression of control in the config file.
        """
        from gep.pipeline import STAGE_REGISTRY, TERMINAL_STAGES

        stages = params.get("pipeline")
        if not stages:
            raise ConfigError(f"archetype {name}: 'pipeline' is required and must be non-empty")
        for stage_id in stages:
            if stage_id in TERMINAL_STAGES:
                raise ConfigError(
                    f"archetype {name}: stage {stage_id!r} is always run last and "
                    f"must not be listed in 'pipeline'"
                )
            if stage_id not in STAGE_REGISTRY:
                raise ConfigError(f"archetype {name}: unknown pipeline stage {stage_id!r}")
        if len(set(stages)) != len(stages):
            raise ConfigError(f"archetype {name}: 'pipeline' lists a stage more than once")

        # Feature stages overwrite what macro_layout wrote, so scheduling one
        # before it means the layout silently erases the feature. That failure
        # produces a floor with no river and no error, which is the worst shape
        # a config mistake can take.
        for feature in ("rivers", "chambers"):
            if feature in stages:
                if "macro_layout" not in stages:
                    raise ConfigError(
                        f"archetype {name}: stage {feature!r} overwrites the macro "
                        f"layout, so 'macro_layout' must also be in 'pipeline'"
                    )
                if stages.index(feature) < stages.index("macro_layout"):
                    raise ConfigError(
                        f"archetype {name}: stage {feature!r} must come after "
                        f"'macro_layout' -- it overwrites what the layout wrote, so "
                        f"running it first would leave no trace of the feature"
                    )
        if params.get("prefabs") and "prefabs" not in stages:
            raise ConfigError(
                f"archetype {name}: declares prefabs but its pipeline omits the 'prefabs' stage"
            )

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
        for pair in params.get("forbid_adjacent_biomes", []):
            refs += pair
        if params.get("fallback_biome"):
            refs.append(params["fallback_biome"])
        # Feature stages name biomes outside the layout block; without these a
        # typo'd river biome would only surface as a KeyError mid-generation.
        if params.get("rivers"):
            refs.append(params["rivers"]["biome"])
        if params.get("chambers"):
            refs.append(params["chambers"]["wall_biome"])
        return refs
