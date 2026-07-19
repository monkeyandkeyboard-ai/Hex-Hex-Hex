import pathlib
import random

import pytest

from gep.config_loader import ConfigStore
from gep.rewards import KIND_EQUIPMENT, KIND_ITEMS, NOTHING, RewardError, RewardService

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(scope="module")
def store():
    return ConfigStore(CONFIG_DIR)


@pytest.fixture(scope="module")
def rewards(store):
    return store.rewards


def service(tables, profiles=None, items=None):
    """A service over ad-hoc config, for testing roll behaviour in isolation."""
    return RewardService(profiles or {}, tables, items)


def profile(table_id, rolls=1):
    return {"P": {"slots": [{"rolls": rolls, "table": table_id}]}}


# --- item tables -----------------------------------------------------------

def test_nothing_entries_produce_no_reward():
    tables = {"T": {"kind": KIND_ITEMS, "entries": [[NOTHING, 1]]}}
    svc = service(tables, profile("T", 5))
    assert svc.generate("P", random.Random(1)) == []


def test_certain_table_always_pays_out():
    tables = {"T": {"kind": KIND_ITEMS, "entries": [["copper_ore", 1]]}}
    svc = service(tables, profile("T", 3))
    out = svc.generate("P", random.Random(2))
    assert [r["item_id"] for r in out] == ["copper_ore"] * 3
    assert all(r["quantity"] == 1 for r in out)


def test_empty_profile_is_safe(rewards):
    assert rewards.generate("EMPTY", random.Random(3)) == []


def test_zero_total_weight_pays_nothing():
    """A table whose weights are all zero must not fall through to the last
    entry, which is what a naive cumulative scan does."""
    tables = {"T": {"kind": KIND_ITEMS, "entries": [["copper_ore", 0], ["iron_ore", 0]]}}
    svc = service(tables, profile("T", 4))
    assert svc.generate("P", random.Random(4)) == []


def test_rolls_controls_attempt_count():
    tables = {"T": {"kind": KIND_ITEMS, "entries": [["copper_ore", 1]]}}
    assert len(service(tables, profile("T", 7)).generate("P", random.Random(5))) == 7
    assert service(tables, profile("T", 0)).generate("P", random.Random(5)) == []


def test_weights_shift_the_distribution():
    """Rare entries must actually be rare -- a table that ignores its weights
    would pass every other test in this file."""
    tables = {"T": {"kind": KIND_ITEMS, "entries": [[NOTHING, 9], ["copper_ore", 1]]}}
    svc = service(tables, profile("T", 1000))
    out = svc.generate("P", random.Random(7))
    assert 50 < len(out) < 150, f"expected ~10%, got {len(out)/10:.1f}%"


def test_quantity_range_is_respected():
    tables = {"T": {
        "kind": KIND_ITEMS,
        "entries": [["copper_ore", 1]],
        "quantities": {"copper_ore": [2, 5]},
    }}
    out = service(tables, profile("T", 50)).generate("P", random.Random(8))
    assert out
    assert all(2 <= r["quantity"] <= 5 for r in out)
    assert len({r["quantity"] for r in out}) > 1, "quantity should actually vary"


# --- profiles --------------------------------------------------------------

def test_unknown_profile_is_an_error(rewards):
    with pytest.raises(RewardError, match="unknown reward profile"):
        rewards.generate("NOPE", random.Random(9))


def test_documentation_keys_are_not_profiles(rewards):
    """Keys prefixed with _ are comments, not rollable profiles."""
    with pytest.raises(RewardError, match="unknown reward profile"):
        rewards.generate("_comment", random.Random(10))


def test_unknown_table_is_an_error():
    with pytest.raises(RewardError, match="unknown loot table"):
        service({}, profile("MISSING")).generate("P", random.Random(11))


def test_slots_roll_independently():
    """Two slots against different tables must both contribute -- the whole
    point of splitting materials from equipment."""
    tables = {
        "A": {"kind": KIND_ITEMS, "entries": [["copper_ore", 1]]},
        "B": {"kind": KIND_ITEMS, "entries": [["iron_ore", 1]]},
    }
    profiles = {"P": {"slots": [
        {"rolls": 2, "table": "A"}, {"rolls": 3, "table": "B"},
    ]}}
    out = service(tables, profiles).generate("P", random.Random(12))
    ids = [r["item_id"] for r in out]
    assert ids.count("copper_ore") == 2
    assert ids.count("iron_ore") == 3


def test_roll_table_bypasses_profiles(rewards):
    """The lower-level entry point, for sources whose shape is computed."""
    out = rewards.roll_table("BULK_MATS", 5, random.Random(13))
    assert len(out) == 5


# --- equipment tables ------------------------------------------------------

def test_equipment_never_drops_at_zero_chance(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 0.0}}
    svc = service(tables, profile("E", 100), store.items)
    assert svc.generate("P", random.Random(14)) == []


def test_equipment_always_drops_at_full_chance(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0}}
    out = service(tables, profile("E", 20), store.items).generate("P", random.Random(15))
    assert len(out) == 20
    assert all(r["kind"] == KIND_EQUIPMENT for r in out)
    assert all(r["quantity"] == 1 for r in out), "equipment must never stack"


def test_equipment_drop_chance_is_honoured(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 0.25}}
    out = service(tables, profile("E", 2000), store.items).generate("P", random.Random(16))
    assert 400 < len(out) < 600, f"expected ~25%, got {len(out)/20:.1f}%"


def test_equipment_respects_tier_filter(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0, "min_tier": 4, "max_tier": 6}}
    out = service(tables, profile("E", 50), store.items).generate("P", random.Random(17))
    for reward in out:
        assert 4 <= store.items.runtime_stats(reward["item_id"])["tier"] <= 6


def test_equipment_respects_slot_filter(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0, "slots": ["feet", "head"]}}
    out = service(tables, profile("E", 40), store.items).generate("P", random.Random(18))
    for reward in out:
        assert store.items.runtime_stats(reward["item_id"])["equipment_slot"] in ("feet", "head")


def test_low_tier_bases_are_commoner_than_high(store):
    """drop_tier on the base is the only rarity knob, so a wide-open
    equipment table must still produce mostly Tier 1 items."""
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0}}
    out = service(tables, profile("E", 1500), store.items).generate("P", random.Random(19))
    tiers = [store.items.runtime_stats(r["item_id"])["tier"] for r in out]
    assert tiers.count(1) > tiers.count(3) > tiers.count(6)


def test_equipment_without_registry_is_an_error():
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0}}
    with pytest.raises(RewardError, match="without an item registry"):
        service(tables, profile("E"), None).generate("P", random.Random(20))


def test_impossible_filter_pays_nothing(store):
    tables = {"E": {"kind": KIND_EQUIPMENT, "drop_chance": 1.0, "slots": ["nope"]}}
    svc = service(tables, profile("E", 10), store.items)
    assert svc.generate("P", random.Random(21)) == []


# --- source independence ---------------------------------------------------

def test_service_needs_no_entity_to_generate(rewards):
    """The decoupling claim, tested directly: a full payout with no monster,
    no player, no floor, and no tick engine in scope."""
    out = rewards.generate("GOBLIN_DROPS", random.Random(22))
    assert isinstance(out, list)
    for reward in out:
        assert set(reward) == {"kind", "item_id", "quantity"}


def test_container_and_monster_use_the_same_call(rewards):
    """A chest profile and a monster profile are rolled by one code path;
    only the config differs."""
    chest = rewards.generate("WOODEN_CHEST", random.Random(23))
    monster = rewards.generate("GOBLIN_DROPS", random.Random(23))
    for reward in chest + monster:
        assert reward["kind"] in (KIND_ITEMS, KIND_EQUIPMENT)
    # The chest's equipment table is drop_chance 1.0, so it always yields gear.
    assert any(r["kind"] == KIND_EQUIPMENT for r in chest)


def test_profiles_are_reusable_across_sources(rewards):
    """Two sources pointed at one profile must roll identically given the
    same seed -- profiles carry no source identity."""
    a = rewards.generate("CAVE_RAT_DROPS", random.Random(24))
    b = rewards.generate("CAVE_RAT_DROPS", random.Random(24))
    assert a == b


def test_generation_is_deterministic_for_a_seed(rewards):
    assert rewards.generate("WOODEN_CHEST", random.Random(25)) == \
           rewards.generate("WOODEN_CHEST", random.Random(25))


# --- shipped config --------------------------------------------------------

def test_every_shipped_profile_rolls(store):
    rng = random.Random(26)
    for profile_id in store.reward_profiles:
        if profile_id.startswith("_"):
            continue
        for _ in range(50):
            for reward in store.rewards.generate(profile_id, rng):
                assert reward["quantity"] >= 1
                if reward["kind"] == KIND_EQUIPMENT:
                    store.items.runtime_stats(reward["item_id"])


def test_every_monster_reward_table_resolves(store):
    rng = random.Random(27)
    for monster_id, template in store.monsters.items():
        for _ in range(20):
            store.rewards.generate(template["reward_table"], rng)
