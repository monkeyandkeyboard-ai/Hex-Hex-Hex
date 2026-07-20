import pathlib
import random

import pytest

from gep.config_loader import ConfigStore
from gep.items import (
    MAX_MOD_VALUE,
    MAX_TIER,
    PREFIX,
    SUFFIX,
    ItemError,
    ItemRegistry,
    encode_instance,
    family_of,
    is_instance,
    parse_instance,
)

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(scope="module")
def store():
    return ConfigStore(CONFIG_DIR)


@pytest.fixture(scope="module")
def items(store):
    return store.items


# --- codec -----------------------------------------------------------------

def test_encode_parse_round_trip():
    mods = [
        {"affix": PREFIX, "code": "st1", "value": 2},
        {"affix": PREFIX, "code": "cn3", "value": 5},
        {"affix": SUFFIX, "code": "dx2", "value": 4},
    ]
    encoded = encode_instance("M1", mods)
    assert encoded == "M1:2P1S:Pst1002Pcn3005Sdx2004"

    parsed = parse_instance(encoded)
    assert parsed["base_code"] == "M1"
    assert parsed["mods"] == mods


def test_encode_zero_pads_values():
    encoded = encode_instance("D9", [{"affix": PREFIX, "code": "cc1", "value": 7}])
    assert encoded.endswith("Pcc1007")


def test_round_trip_handles_no_modifiers():
    encoded = encode_instance("CF1", [])
    assert encoded == "CF1:0P0S:"
    assert parse_instance(encoded) == {"base_code": "CF1", "mods": []}


def test_variable_length_base_codes_round_trip():
    """Base codes are 2 or 3 characters; the codec must not assume a width."""
    for code in ("M1", "CF1", "PG9", "CK5"):
        mods = [{"affix": SUFFIX, "code": "ar4", "value": 11}]
        assert parse_instance(encode_instance(code, mods))["base_code"] == code


def test_value_out_of_range_is_rejected():
    with pytest.raises(ItemError, match="out of range"):
        encode_instance("M1", [{"affix": PREFIX, "code": "st1", "value": MAX_MOD_VALUE + 1}])


def test_unknown_affix_marker_is_rejected():
    with pytest.raises(ItemError, match="unknown affix"):
        encode_instance("M1", [{"affix": "X", "code": "st1", "value": 1}])


@pytest.mark.parametrize("bad", [
    "",
    "M1",                          # no field separators
    "M1:2P1S",                     # missing modifier field
    "M1:banana:Pst1002",           # malformed counts
    "M1:1P0S:Pst100",              # chunk truncated mid-value
    "M1:1P0S:Xst1002",             # unknown affix marker
    "M1:1P0S:Pst1abc",             # non-numeric value
])
def test_malformed_strings_are_rejected(bad):
    with pytest.raises(ItemError):
        parse_instance(bad)


def test_declared_counts_must_match_chunks():
    """A truncation that lands on a chunk boundary is only catchable by
    checking the declared counts, which is why they are encoded at all."""
    with pytest.raises(ItemError, match="disagree"):
        parse_instance("M1:2P1S:Pst1002Sdx2004")


def test_is_instance_distinguishes_ids():
    assert is_instance("M1:1P0S:Pst1002")
    assert not is_instance("copper_ore")
    assert not is_instance("M1")


# --- rolling ---------------------------------------------------------------

TIER_SLOTS = {
    1: (1, 0), 2: (1, 1), 3: (2, 1),
    4: (2, 2), 5: (3, 2), 6: (3, 3),
    7: (3, 3), 8: (3, 3), 9: (3, 3),
}


def test_tier_determines_mod_slot_counts(items):
    """The confirmed power curve: every tier 1-6 adds a slot, 6 is the
    ceiling, and 7-9 hold it."""
    rng = random.Random(100)
    by_tier = {}
    for code, base in items.bases.items():
        by_tier.setdefault(int(base["Tier"]), code)

    for tier, base_code in sorted(by_tier.items()):
        parsed = parse_instance(items.roll_instance(base_code, rng))
        prefixes = sum(1 for m in parsed["mods"] if m["affix"] == PREFIX)
        suffixes = sum(1 for m in parsed["mods"] if m["affix"] == SUFFIX)
        assert (prefixes, suffixes) == TIER_SLOTS[tier], (
            f"tier {tier} base {base_code} rolled {prefixes}P{suffixes}S"
        )


def test_mod_count_never_exceeds_six(items):
    rng = random.Random(101)
    for code in items.bases:
        mods = parse_instance(items.roll_instance(code, rng))["mods"]
        assert len(mods) <= 6


def test_stats_are_distinct_within_an_item(items):
    rng = random.Random(102)
    for code in list(items.bases)[:60]:
        stats = [m["stat"] for m in items.runtime_stats(items.roll_instance(code, rng))["mods"]]
        assert len(stats) == len(set(stats)), f"{code} rolled a duplicate stat: {stats}"


# --- affix families --------------------------------------------------------
#
# A family is the exclusion unit when rolling. No modifier in the shipped
# config declares one yet, so these build registries with families injected:
# the mechanism has to be proven before content leans on it, and the
# defaulting behaviour has to be proven so that adding a family to one
# modifier cannot disturb every other.

def _with_families(items, assignment):
    """A registry whose modifiers carry the given stat -> family mapping."""
    modifiers = [
        {**m, "family": assignment[m["stat"]]} if m["stat"] in assignment else m
        for m in items.modifiers
    ]
    return ItemRegistry(items.bases, modifiers, items.generation,
                        items.known_stats, items.item_names)


def test_family_defaults_to_the_stat(items):
    """With no families declared, every stat is its own family -- which is
    what makes today's config behave exactly as it did before families."""
    assert family_of({"stat": "strength", "tier": 1}) == "strength"
    assert family_of({"stat": "strength", "family": "offense"}) == "offense"
    assert set(items.modifiers_by_family) == {m["stat"] for m in items.modifiers}


def test_grouped_stats_compete_for_one_slot(items):
    """The opportunity cost. Strength, arcana and precision share a family, so
    an item may carry at most one of the three no matter how many slots it
    has."""
    grouped = _with_families(items, {
        "strength": "offense", "arcana": "offense", "precision": "offense",
    })
    rng = random.Random(202)
    for code in list(grouped.bases)[:60]:
        stats = [m["stat"] for m in grouped.runtime_stats(grouped.roll_instance(code, rng))["mods"]]
        offense = [s for s in stats if s in ("strength", "arcana", "precision")]
        assert len(offense) <= 1, f"{code} rolled {offense} from one family"


def test_ungrouped_stats_are_unaffected_by_someone_elses_family(items):
    """Declaring a family must not change how unrelated modifiers roll."""
    grouped = _with_families(items, {"strength": "offense", "arcana": "offense"})
    rng = random.Random(203)
    for code in list(grouped.bases)[:60]:
        stats = [m["stat"] for m in grouped.runtime_stats(grouped.roll_instance(code, rng))["mods"]]
        others = [s for s in stats if s not in ("strength", "arcana")]
        assert len(others) == len(set(others)), f"{code} duplicated {others}"


def test_families_shrink_the_number_of_modifiers_an_item_can_carry(items):
    """Collapsing every stat into one family leaves exactly one slot usable --
    the exclusion is real, not cosmetic."""
    one_family = _with_families(items, {s: "everything" for s in items.known_stats})
    rng = random.Random(204)
    for code in list(one_family.bases)[:40]:
        mods = parse_instance(one_family.roll_instance(code, rng))["mods"]
        assert len(mods) <= 1


def test_distinct_families_off_allows_repeats(items):
    one_family = ItemRegistry(
        items.bases, items.modifiers,
        {**items.generation, "distinct_families": False},
        items.known_stats, items.item_names,
    )
    rng = random.Random(205)
    repeated = False
    for code in list(one_family.bases)[:60]:
        stats = [m["stat"] for m in
                 one_family.runtime_stats(one_family.roll_instance(code, rng))["mods"]]
        if len(stats) != len(set(stats)):
            repeated = True
            break
    assert repeated, "distinct_families=False never produced a repeat"


def test_modifier_tier_respects_the_configured_cap_policy(items):
    """Which tiers a base may roll is `modifier_tier_cap`, so this asserts
    whichever invariant the configured policy promises rather than assuming
    one. Hardcoding `item_tier` here meant flipping a documented, supported
    config value turned the suite red without anything being wrong.
    """
    policy = items.generation.get("modifier_tier_cap", "item_tier")
    rng = random.Random(103)
    for code, base in items.bases.items():
        item_tier = int(base["Tier"])
        ceiling = item_tier if policy == "item_tier" else MAX_TIER
        for _ in range(4):
            for mod in items.runtime_stats(items.roll_instance(code, rng))["mods"]:
                assert mod["tier"] <= ceiling, (
                    f"{code} (tier {item_tier}, policy {policy}) rolled a "
                    f"tier {mod['tier']} modifier"
                )


def test_item_tier_policy_actually_caps(items):
    """The policy the config is not currently set to still has to work, or
    switching back would be a change nobody had tested."""
    capped = ItemRegistry(
        items.bases, items.modifiers,
        {**items.generation, "modifier_tier_cap": "item_tier"},
        items.known_stats, items.item_names,
    )
    rng = random.Random(103)
    for code, base in capped.bases.items():
        item_tier = int(base["Tier"])
        for _ in range(4):
            for mod in capped.runtime_stats(capped.roll_instance(code, rng))["mods"]:
                assert mod["tier"] <= item_tier


def test_unrestricted_policy_reaches_tiers_above_the_base_tier(items):
    """The point of the configured policy: a low base can jackpot.

    Without this, `unrestricted` could silently behave like `item_tier` and
    only the absence of rare drops would ever hint at it.
    """
    unrestricted = ItemRegistry(
        items.bases, items.modifiers,
        {**items.generation, "modifier_tier_cap": "unrestricted"},
        items.known_stats, items.item_names,
    )
    tier_1_bases = [c for c, b in unrestricted.bases.items() if int(b["Tier"]) == 1]
    rng = random.Random(9)
    seen = []
    for _ in range(300):
        for code in tier_1_bases:
            seen += [m["tier"] for m in
                     unrestricted.runtime_stats(unrestricted.roll_instance(code, rng))["mods"]]
    assert max(seen) > 1, "a tier 1 base never exceeded tier 1 under 'unrestricted'"


def test_modifier_values_stay_within_declared_range(items):
    rng = random.Random(104)
    for code in list(items.bases)[:80]:
        for mod in items.runtime_stats(items.roll_instance(code, rng))["mods"]:
            entry = items.modifier_index[mod["code"]]
            assert entry["min_value"] <= mod["value"] <= entry["max_value"]


def test_low_modifier_tiers_dominate(items):
    """Modifier tier is weighted the same way item rarity is; without that a
    tier 9 roll would be as common as a tier 1."""
    rng = random.Random(105)
    tiers = []
    for _ in range(400):
        tiers += [m["tier"] for m in items.runtime_stats(items.roll_instance("S9", rng))["mods"]]
    assert tiers.count(1) > tiers.count(2) > tiers.count(4)


def test_rolling_is_deterministic_for_a_seed(items):
    a = items.roll_instance("M5", random.Random(7))
    b = items.roll_instance("M5", random.Random(7))
    assert a == b


def test_unknown_base_is_an_error(items):
    with pytest.raises(ItemError, match="unknown item base"):
        items.roll_instance("NOPE1", random.Random(1))


# --- runtime stats ---------------------------------------------------------

def test_runtime_stats_include_implicits(items):
    """A Crude Sword's implicit crit chance must survive into runtime stats
    whether or not any modifier touched that stat."""
    stats = items.runtime_stats(encode_instance("S1", []))
    assert stats["stats"]["critical_strike_chance"] == 5
    assert stats["name"] == "Crude Sword"
    assert stats["tier"] == 1


def test_display_name_is_mad_libs_plus_base_name_and_tier(items):
    stats = items.runtime_stats(encode_instance("S9", []))
    words = stats["display_name"].split(" ")
    assert words[-1] == "T9"
    assert " ".join(words[-3:-1]) == "Apex Sword"          # base name, verbatim
    assert words[0] in items.item_names["adjectives"]
    assert words[1] in items.item_names["nouns"]


def test_display_name_is_deterministic_for_the_same_instance(items):
    """Recomputed fresh from the string on every load (items.md): the name
    must not change between two independent calls for the same instance."""
    encoded = encode_instance("S1", [{"affix": PREFIX, "code": "cc1", "value": 1}])
    assert items.runtime_stats(encoded)["display_name"] == items.runtime_stats(encoded)["display_name"]


def test_runtime_stats_sum_implicit_and_modifier(items):
    """A modifier on the same stat as an implicit adds to it rather than
    replacing it."""
    encoded = encode_instance("S1", [{"affix": PREFIX, "code": "cc1", "value": 1}])
    assert items.runtime_stats(encoded)["stats"]["critical_strike_chance"] == 6


def test_runtime_stats_keep_percent_implicits_separate(items):
    """`constitution_percent` is a multiplier, not flat constitution; folding
    them together would turn +2% into +2."""
    encoded = encode_instance("M1", [{"affix": PREFIX, "code": "cn2", "value": 3}])
    stats = items.runtime_stats(encoded)["stats"]
    assert stats["constitution"] == 3
    assert stats["constitution_percent"] == 0.5


def test_runtime_stats_carry_base_combat_fields(items):
    stats = items.runtime_stats(encode_instance("D1", []))
    assert stats["speed_ticks"] == 2
    assert stats["damage_min"] == 0.10
    assert stats["damage_max"] == 0.35
    assert stats["armor"] == 0


def test_armor_base_carries_armor_and_no_damage(items):
    stats = items.runtime_stats(encode_instance("PT9", []))
    assert stats["armor"] == 108
    assert stats["damage_min"] == 0
    assert stats["damage_max"] == 0


def test_offhand_bases_deal_no_damage(items):
    for code, base in items.bases.items():
        if base["equipment_slot"] == "off_hand":
            assert base["damage_min"] == 0 and base["damage_max"] == 0, code


def test_runtime_stats_reject_unknown_base():
    from gep.items import ItemRegistry
    registry = ItemRegistry({}, [], {"tier_mod_slots": {}}, set())
    with pytest.raises(ItemError, match="unknown base"):
        registry.runtime_stats("ZZ9:0P0S:")


# --- shipped content -------------------------------------------------------

def test_every_base_rolls_and_resolves(items):
    """Smoke test across the whole item space: no base may fail to roll or to
    resolve back into stats."""
    rng = random.Random(999)
    for code in items.bases:
        stats = items.runtime_stats(items.roll_instance(code, rng))
        assert stats["base_code"] == code
        assert stats["name"]


def test_damage_spread_matches_speed_tier(items):
    """The agreed spread-by-speed table. A weapon whose window drifted from
    its speed would quietly rebalance combat."""
    expected = {2: (0.10, 0.35), 3: (0.15, 0.50), 4: (0.25, 0.65), 5: (0.35, 0.80)}
    for code, base in items.bases.items():
        if base["damage_max"] == 0:
            continue  # armour and off-hands
        window = expected[base["speed_ticks"]]
        assert (base["damage_min"], base["damage_max"]) == window, code
