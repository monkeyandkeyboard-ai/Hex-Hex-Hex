"""The stat aggregation pipeline: key grammar, pool semantics, phase order.

The tests that matter most here are the ones pinning *how* pools combine
rather than that they combine at all -- `increased` summing and `more`
multiplying is the distinction the whole design rests on, and either one
quietly behaving like the other is the failure that would not otherwise show
up until someone wondered why two +20% rolls felt different than expected.
"""
import pytest

from gep.statblock import (
    EMPTY,
    FLAT,
    INCREASED,
    MORE,
    ResolvedStats,
    StatBlock,
    build_stats,
    parse_modifier_key,
)


# --- key grammar -----------------------------------------------------------

@pytest.mark.parametrize("key,expected", [
    ("strength", ("strength", FLAT, False)),
    ("strength_percent", ("strength", INCREASED, False)),
    ("strength_more", ("strength", MORE, False)),
    ("local_armor", ("armor", FLAT, True)),
    ("local_armor_percent", ("armor", INCREASED, True)),
    # Stat names containing underscores must survive suffix matching --
    # splitting on the last `_` would turn these into "mana" and "critical".
    ("mana_attunement", ("mana_attunement", FLAT, False)),
    ("critical_strike_chance", ("critical_strike_chance", FLAT, False)),
    ("mana_attunement_percent", ("mana_attunement", INCREASED, False)),
    # A bare suffix names no target, so it is a target in its own right
    # rather than a class marker with nothing to modify.
    ("_percent", ("_percent", FLAT, False)),
])
def test_key_grammar(key, expected):
    assert parse_modifier_key(key) == expected


# --- pool semantics --------------------------------------------------------

def test_flat_sources_sum():
    block = StatBlock()
    block.add("strength", 5)
    block.add("strength", 3)
    assert block.resolve("strength", base=10) == 18


def test_increased_pool_is_additive_not_multiplicative():
    """Two +20% increases are +40%, not 1.2 x 1.2 = +44%."""
    block = StatBlock()
    block.add("strength_percent", 20)
    block.add("strength_percent", 20)
    assert block.resolve("strength", base=100) == pytest.approx(140.0)


def test_more_sources_multiply_independently():
    """Two +20% more are 1.2 x 1.2 = +44%, which is what separates the pool
    from `increased`. If these ever sum, this is the test that says so."""
    block = StatBlock()
    block.add("strength_more", 20)
    block.add("strength_more", 20)
    assert block.resolve("strength", base=100) == pytest.approx(144.0)


def test_reduced_and_less_are_the_same_pools_with_negative_values():
    block = StatBlock()
    block.add("strength_percent", -30)
    block.add("strength_more", -50)
    # (100 * 0.7) * 0.5
    assert block.resolve("strength", base=100) == pytest.approx(35.0)


def test_phase_order_flat_then_increased_then_more():
    """The ordering claim, stated as one number.

    (base 10 + flat 10) * (1 + 100%) * 1.5 = 60. Any other phase order gives
    a different answer, so this pins the sequence rather than the arithmetic.
    """
    block = StatBlock()
    block.add("strength", 10)
    block.add("strength_percent", 100)
    block.add("strength_more", 50)
    assert block.resolve("strength", base=10) == pytest.approx(60.0)


def test_unmodified_target_returns_its_baseline():
    assert StatBlock().resolve("strength", base=7) == 7


def test_merge_preserves_pool_semantics():
    a = StatBlock()
    a.add("strength_percent", 20)
    a.add("strength_more", 20)
    b = StatBlock()
    b.add("strength_percent", 20)
    b.add("strength_more", 20)
    a.merge(b)

    # increased summed into one coefficient, more kept as two discrete factors
    assert a.increased["strength"] == 40
    assert sorted(a.more["strength"]) == [20, 20]
    assert a.resolve("strength", base=100) == pytest.approx(140 * 1.2 * 1.2)


def test_empty_resolved_stats_is_the_identity():
    """An entity whose equipment has not been aggregated must behave exactly
    as it did before this layer existed, not lose its baseline."""
    assert EMPTY.resolve("strength", base=42) == 42


# --- local vs global -------------------------------------------------------

def test_local_increases_scale_local_flat_but_not_global_flat():
    """The reason phases 2 and 3 are separate fields.

    Local: (base 100 + 50) * 1.5 = 225. The global +100 then rides on top
    unscaled, for 325. If local and global flat shared a pool, the global
    bonus would be caught by the local increase and give 375.
    """
    stats = ResolvedStats()
    local = StatBlock()
    local.add("local_armor", 50)
    local.add("local_armor_percent", 50)
    stats.local_totals["armor"] = local.resolve("armor", base=100)
    stats.globals.add("armor", 100)

    assert stats.local_totals["armor"] == pytest.approx(225.0)
    assert stats.resolve("armor") == pytest.approx(325.0)


# --- aggregation over real equipment --------------------------------------

class _FakeRegistry:
    """Stands in for ItemRegistry, keyed by the serialized id.

    `build_stats` only ever asks for `runtime_stats`, so faking it keeps
    these tests about aggregation rather than about the item codec -- which
    test_items.py already covers.
    """

    def __init__(self, by_id):
        self.by_id = by_id

    def runtime_stats(self, serialized):
        return self.by_id[serialized]


def _item(stats, **base):
    return {"stats": stats, **base}


def test_modifiers_pool_across_every_equipped_slot():
    registry = _FakeRegistry({
        "M1:1P0S:Pst1005": _item({"strength": 5}),
        "H1:1P0S:Pst2003": _item({"strength": 3, "strength_percent": 50}),
    })
    stats = build_stats(["M1:1P0S:Pst1005", "H1:1P0S:Pst2003", None], registry)

    # (baseline 10 + 5 + 3) * 1.5
    assert stats.resolve("strength", base=10) == pytest.approx(27.0)


def test_locals_resolve_per_item_before_being_summed():
    """Two items each with local armor keep their bonuses to themselves.

    Item A: (20 + 10) * 2.0 = 60. Item B: (100 + 0) * 1.0 = 100.
    If locals pooled globally first, A's +100% would scale B's base too.
    """
    registry = _FakeRegistry({
        "A:0P0S:": _item({"local_armor": 10, "local_armor_percent": 100}, armor=20),
        "B:0P0S:": _item({"local_armor": 0}, armor=100),
    })
    stats = build_stats(["A:0P0S:", "B:0P0S:"], registry)
    assert stats.local_totals["armor"] == pytest.approx(160.0)


def test_non_instance_equipment_ids_contribute_nothing():
    """`unarmed` and friends are registry entries, not rolled instances."""
    registry = _FakeRegistry({})
    stats = build_stats(["unarmed", None], registry)
    assert stats.resolve("strength", base=10) == 10


def test_item_referencing_a_deleted_base_is_skipped_not_fatal():
    """A stale saved item must not stop the rest of the gear aggregating --
    or, worse, stop the player logging in."""
    from gep.items import ItemError

    class _Broken(_FakeRegistry):
        def runtime_stats(self, serialized):
            if serialized == "GONE:0P0S:":
                raise ItemError("unknown base")
            return super().runtime_stats(serialized)

    registry = _Broken({"M1:1P0S:Pst1005": _item({"strength": 5})})
    stats = build_stats(["GONE:0P0S:", "M1:1P0S:Pst1005"], registry)
    assert stats.resolve("strength", base=10) == 15
