"""Item bases, instance rolling, and the serialized-instance codec (items.md).

Three things live here and nothing else does:

  * the **registry** of item bases loaded from config/items/*_bases.json,
  * **rolling** a base into a unique instance (which modifiers, what values),
  * the **codec** that turns an instance into a compact string and back.

The GEP is the sole authority for both directions of that codec, so nothing
outside this module may build or hand-parse an instance string. Combat and
inventory ask for `runtime_stats()` and get a plain dict; they never see the
encoding. That is what keeps the string format changeable without touching a
combat file.

Serialized instance format
--------------------------

    M1:2P1S:Pst1002Pcn3005Sdx2004

    ^^ base code    ^^^^ slot counts   ^^^^^^^ modifier chunks (7 chars each)

Component blocks are joined with `|` (items.md §3.A.2) -- enchantments and
runes become blocks two and three when those systems exist. Only the primary
block is emitted today, so an instance carries no trailing delimiters.

The primary block is colon-delimited rather than purely positional because
base codes are not fixed width (`M1` vs `CF1` vs `PG9`). Two delimiter
characters buys unambiguous parsing; a positional scheme would have to pad
every code and would break the first time a 4-character base appears.

Each modifier chunk is:

    P st1 002
    ^  ^   ^-- value, zero-padded to 3 digits
    |  +------ modifier code from modifiers.json (stat + tier)
    +--------- P refix or S uffix

The modifier code already encodes stat *and* tier, so the chunk does not
repeat the tier as a separate field the way items.md sketched -- one source
of truth for a modifier's identity, and the chunk stays 7 characters.
"""
import random
import re

from gep.rolls import weighted_pick

# --- codec constants -------------------------------------------------------

BLOCK_SEP = "|"
FIELD_SEP = ":"
PREFIX = "P"
SUFFIX = "S"
MOD_CODE_LEN = 3
MOD_VALUE_DIGITS = 3
MOD_CHUNK_LEN = 1 + MOD_CODE_LEN + MOD_VALUE_DIGITS  # 7
MAX_MOD_VALUE = 10 ** MOD_VALUE_DIGITS - 1

_COUNTS_RE = re.compile(r"^(\d+)P(\d+)S$")

# Note on modifier keys: `runtime_stats()` returns the stats dict keyed
# exactly as config wrote it (`strength`, `strength_percent`, `local_armor`),
# and deliberately does not sum across those classes -- doing so would
# silently turn "+2% constitution" into "+2 constitution". What each key
# means, and the order the classes combine in, is gep/statblock.py's job.


class ItemError(Exception):
    pass


def family_of(modifier: dict) -> str:
    """The exclusion group a modifier belongs to.

    Defaults to the modifier's stat, so a modifiers.json with no `family`
    fields at all behaves exactly as it did when the rule was "never roll the
    same stat twice". Declaring a family is how two different stats are made
    to compete for the same slot -- which is the opportunity cost that stops
    an item rolling every desirable modifier at once.
    """
    return modifier.get("family") or modifier["stat"]


# --- codec -----------------------------------------------------------------

def encode_instance(base_code: str, mods: list[dict]) -> str:
    """Build the serialized string for a rolled instance.

    `mods` is an ordered list of {affix, code, value}. Order is preserved so
    that decoding round-trips exactly; prefixes are not required to precede
    suffixes, though the roller emits them that way.
    """
    prefixes = sum(1 for m in mods if m["affix"] == PREFIX)
    suffixes = sum(1 for m in mods if m["affix"] == SUFFIX)

    chunks = []
    for mod in mods:
        affix = mod["affix"]
        if affix not in (PREFIX, SUFFIX):
            raise ItemError(f"unknown affix marker {affix!r}")
        code = mod["code"]
        if len(code) != MOD_CODE_LEN:
            raise ItemError(f"modifier code {code!r} must be {MOD_CODE_LEN} characters")
        value = int(mod["value"])
        if not 0 <= value <= MAX_MOD_VALUE:
            raise ItemError(f"modifier value {value} out of range 0..{MAX_MOD_VALUE}")
        chunks.append(f"{affix}{code}{value:0{MOD_VALUE_DIGITS}d}")

    return FIELD_SEP.join([base_code, f"{prefixes}P{suffixes}S", "".join(chunks)])


def parse_instance(serialized: str) -> dict:
    """Decode a serialized instance into {base_code, mods}.

    Raises ItemError on anything malformed rather than returning a partial
    item: a truncated string must not silently become a weaker but valid
    weapon. The declared slot counts are verified against the chunks actually
    present, which is what catches truncation that happens to land on a
    chunk boundary.
    """
    if not isinstance(serialized, str) or not serialized:
        raise ItemError("serialized item must be a non-empty string")

    primary = serialized.split(BLOCK_SEP)[0]
    parts = primary.split(FIELD_SEP)
    if len(parts) != 3:
        raise ItemError(f"malformed primary block: {primary!r}")

    base_code, counts, mod_data = parts

    if not base_code:
        raise ItemError(f"missing base code in {primary!r}")

    match = _COUNTS_RE.match(counts)
    if not match:
        raise ItemError(f"malformed slot counts {counts!r}")
    declared_prefixes, declared_suffixes = int(match.group(1)), int(match.group(2))

    if len(mod_data) % MOD_CHUNK_LEN != 0:
        raise ItemError(
            f"modifier data length {len(mod_data)} is not a multiple of {MOD_CHUNK_LEN}"
        )

    mods = []
    for i in range(0, len(mod_data), MOD_CHUNK_LEN):
        chunk = mod_data[i:i + MOD_CHUNK_LEN]
        affix = chunk[0]
        if affix not in (PREFIX, SUFFIX):
            raise ItemError(f"unknown affix marker {affix!r} in chunk {chunk!r}")
        code = chunk[1:1 + MOD_CODE_LEN]
        raw_value = chunk[1 + MOD_CODE_LEN:]
        if not raw_value.isdigit():
            raise ItemError(f"non-numeric modifier value in chunk {chunk!r}")
        mods.append({"affix": affix, "code": code, "value": int(raw_value)})

    actual_prefixes = sum(1 for m in mods if m["affix"] == PREFIX)
    actual_suffixes = sum(1 for m in mods if m["affix"] == SUFFIX)
    if (actual_prefixes, actual_suffixes) != (declared_prefixes, declared_suffixes):
        raise ItemError(
            f"slot counts {counts!r} disagree with modifier data "
            f"({actual_prefixes}P{actual_suffixes}S)"
        )

    return {"base_code": base_code, "mods": mods}


def is_instance(item_id: str) -> bool:
    """True if this id is a rolled equipment instance rather than a plain
    template id (`copper_ore`) or bare base code (`M1`)."""
    return isinstance(item_id, str) and FIELD_SEP in item_id


# --- registry --------------------------------------------------------------

BASE_REQUIRED_KEYS = {
    "name",
    "equipment_slot",
    "type",
    "Tier",
    "armor",
    "damage_min",
    "damage_max",
    "speed_ticks",
    "base_sell_value",
    "max_stack",
    "drop_tier",
    "item_level",
    "required_level",
    "description",
    "implicits",
}

MIN_TIER = 1
MAX_TIER = 9


class ItemRegistry:
    """Every item base, plus the modifier pool and generation rules.

    Built once at startup by ConfigStore. Validation is deliberately loud and
    up-front: a base whose implicit names a stat that does not exist is a
    content bug that would otherwise surface as an item that quietly grants
    nothing, months later.
    """

    def __init__(
        self, bases: dict, modifiers: list, generation: dict, known_stats: set[str],
        item_names: dict | None = None,
    ):
        self.bases = bases
        self.modifiers = modifiers
        self.generation = generation
        self.known_stats = known_stats
        self.item_names = item_names or {}

        self.modifier_index = {m["modifier_code"]: m for m in modifiers}

        # stat -> [(modifier_code, weight, tier)], for tier-capped rolling.
        # family -> [modifier entries]. A family is the exclusion unit when
        # rolling: an item takes at most one modifier from each. `family` is
        # optional in config and defaults to the modifier's own stat, which
        # makes "one roll per stat" the degenerate case of "one roll per
        # family" rather than a second, separate rule -- and means grouping
        # stats into a family is purely additive to existing config.
        self.modifiers_by_family: dict[str, list[dict]] = {}
        for mod in modifiers:
            self.modifiers_by_family.setdefault(family_of(mod), []).append(mod)

        self.slots: dict[str, list[str]] = {}
        for code, base in bases.items():
            self.slots.setdefault(base["equipment_slot"], []).append(code)

        # runtime_stats is a pure function of the instance string and this
        # registry's (immutable) bases and modifiers, yet it was recomputed --
        # reparsing the string and reseeding a fresh random.Random for the
        # flavour name -- for every inventory and equipment slot of every
        # connected player, every tick (see server.build_broadcasts). The
        # string is a stable identifier, so the result is cached against it.
        self._runtime_cache: dict[str, dict] = {}

    # -- selection ---------------------------------------------------------

    def drop_candidates(
        self,
        slots: list[str] | None = None,
        min_tier: int = MIN_TIER,
        max_tier: int = MAX_TIER,
        types: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """The (base_code, weight) pairs a loot roll picks from.

        `drop_tier` on the base is the weight, so rarity is tuned entirely in
        the item files -- a loot table never restates a drop chance that could
        drift out of agreement with the base.
        """
        candidates = []
        for code, base in self.bases.items():
            if slots and base["equipment_slot"] not in slots:
                continue
            if types and base["type"] not in types:
                continue
            tier = int(base["Tier"])
            if not min_tier <= tier <= max_tier:
                continue
            weight = float(base["drop_tier"])
            if weight > 0:
                candidates.append((code, weight))
        return sorted(candidates)

    # -- rolling -----------------------------------------------------------

    def _modifier_tier_ceiling(self, item_tier: int) -> int:
        policy = self.generation.get("modifier_tier_cap", "item_tier")
        return item_tier if policy == "item_tier" else MAX_TIER

    def roll_instance(self, base_code: str, rng: random.Random | None = None) -> str:
        """Roll a base into a unique instance and return its serialized string."""
        base = self.bases.get(base_code)
        if base is None:
            raise ItemError(f"unknown item base {base_code!r}")

        roll = rng or random
        tier = int(base["Tier"])
        slots = self.generation["tier_mod_slots"][str(tier)]
        n_prefix = min(int(slots["prefixes"]), int(self.generation["max_prefixes"]))
        n_suffix = min(int(slots["suffixes"]), int(self.generation["max_suffixes"]))

        ceiling = self._modifier_tier_ceiling(tier)
        pool = {
            family: [m for m in entries if int(m["tier"]) <= ceiling]
            for family, entries in self.modifiers_by_family.items()
        }
        pool = {family: entries for family, entries in pool.items() if entries}
        if not pool:
            return encode_instance(base_code, [])

        wanted = n_prefix + n_suffix
        family_names = sorted(pool)

        if self.generation.get("distinct_families", True):
            # Fewer families than slots means fewer modifiers, not a repeat:
            # two rolls from one family on one item read as a bug to a player,
            # and the exclusion is the opportunity cost that makes a slot a
            # decision rather than a wishlist.
            chosen = roll.sample(family_names, min(wanted, len(family_names)))
        else:
            chosen = [roll.choice(family_names) for _ in range(wanted)]

        mods = []
        for index, family in enumerate(chosen):
            # Weighted across every modifier in the family at once, so a
            # family spanning several stats picks the stat and the tier in one
            # draw -- the weights already encode tier rarity, and a two-step
            # draw would let family size quietly distort it.
            entries = pool[family]
            code = weighted_pick(
                roll.random(),
                [(m["modifier_code"], float(m["weight"])) for m in entries],
            )
            entry = self.modifier_index[code]
            value = roll.randint(int(entry["min_value"]), int(entry["max_value"]))
            mods.append({
                "affix": PREFIX if index < n_prefix else SUFFIX,
                "code": code,
                "value": min(value, MAX_MOD_VALUE),
            })

        return encode_instance(base_code, mods)

    # -- reading -----------------------------------------------------------

    def base_of(self, item_id: str) -> dict | None:
        """The base definition behind an id, whether that id is a rolled
        instance or a bare base code. None if the id names neither -- a
        material id lands here and must not raise.
        """
        if is_instance(item_id):
            try:
                code = parse_instance(item_id)["base_code"]
            except ItemError:
                return None
        else:
            code = item_id
        return self.bases.get(code)

    def declared_slot(self, item_id: str) -> str | None:
        """The `equipment_slot` an item declares, verbatim -- `two_hand`
        included. Callers map that onto a body slot; see TWO_HAND in
        gep/entities.py for why it is not one itself."""
        base = self.base_of(item_id)
        return base["equipment_slot"] if base else None

    def _flavor_name(self, serialized: str, base_name: str, tier: int) -> str:
        """'{adjective} {noun} {base name} T{tier}'. Seeded from the item's
        own serialized string rather than the ambient RNG: runtime_stats() is
        recomputed from that string on every load and never persists
        anything of its own, so the name must be a pure function of the
        string or it would change every time the item was looked at."""
        adjectives = self.item_names.get("adjectives") or ["Unnamed"]
        nouns = self.item_names.get("nouns") or ["Item"]
        rng = random.Random(serialized)
        return f"{rng.choice(adjectives)} {rng.choice(nouns)} {base_name} T{tier}"

    def combat_profile(self, item_id: str) -> dict | None:
        """The base fields combat needs to swing: weapon archetype, and the
        damage_min/max multiplier and speed_ticks it carries. Deliberately
        not runtime_stats() -- that requires a rolled instance string, and a
        bare base code (or a non-item equipment id) must resolve here too.
        """
        base = self.base_of(item_id)
        if base is None:
            return None
        return {
            "type": base["type"],
            "damage_min": base["damage_min"],
            "damage_max": base["damage_max"],
            "speed_ticks": base["speed_ticks"],
        }

    def runtime_stats(self, serialized: str) -> dict:
        """Fully resolved, transient stats for an instance (items.md §3.A.5).

        Recomputed from the string on first sight and never persisted, so the
        string stays the single stored truth about an item. Cached against the
        string thereafter: the result is a pure function of it, so a returned
        dict is shared and read-only -- callers describe items from it and must
        not mutate it (none do).
        """
        cached = self._runtime_cache.get(serialized)
        if cached is None:
            cached = self._compute_runtime_stats(serialized)
            self._runtime_cache[serialized] = cached
        return cached

    def _compute_runtime_stats(self, serialized: str) -> dict:
        parsed = parse_instance(serialized)
        base = self.bases.get(parsed["base_code"])
        if base is None:
            raise ItemError(f"instance references unknown base {parsed['base_code']!r}")

        stats: dict[str, float] = {}
        for stat, value in (base.get("implicits") or {}).items():
            stats[stat] = stats.get(stat, 0) + value

        resolved_mods = []
        for mod in parsed["mods"]:
            entry = self.modifier_index.get(mod["code"])
            if entry is None:
                raise ItemError(f"instance references unknown modifier {mod['code']!r}")
            stat = entry["stat"]
            stats[stat] = stats.get(stat, 0) + mod["value"]
            resolved_mods.append({
                "affix": mod["affix"],
                "code": mod["code"],
                "stat": stat,
                "tier": entry["tier"],
                "value": mod["value"],
            })

        tier = int(base["Tier"])
        return {
            "base_code": parsed["base_code"],
            "name": base["name"],
            "display_name": self._flavor_name(serialized, base["name"], tier),
            "equipment_slot": base["equipment_slot"],
            "type": base["type"],
            "tier": tier,
            "damage_min": base["damage_min"],
            "damage_max": base["damage_max"],
            "speed_ticks": base["speed_ticks"],
            "armor": base["armor"],
            "base_sell_value": base["base_sell_value"],
            "max_stack": base["max_stack"],
            "stats": stats,
            "mods": resolved_mods,
        }
