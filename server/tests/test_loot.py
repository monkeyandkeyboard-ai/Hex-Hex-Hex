import random

import pytest

from gep.loot import NOTHING, roll_loot


def test_nothing_entries_produce_no_drop():
    template = {"loot_table": [[NOTHING, 1]], "loot_rolls": 5}
    assert roll_loot(template, random.Random(1)) == []


def test_certain_table_always_drops():
    template = {"loot_table": [["copper_ore", 1]], "loot_rolls": 3}
    assert roll_loot(template, random.Random(2)) == ["copper_ore"] * 3


def test_missing_or_empty_table_is_safe():
    assert roll_loot({}, random.Random(3)) == []
    assert roll_loot({"loot_table": []}, random.Random(3)) == []


def test_zero_total_weight_drops_nothing():
    """A table whose weights are all zero must not fall through to the last
    entry, which is what a naive cumulative scan does."""
    template = {"loot_table": [["copper_ore", 0], ["iron_ore", 0]], "loot_rolls": 4}
    assert roll_loot(template, random.Random(4)) == []


def test_loot_rolls_controls_attempt_count():
    template = {"loot_table": [["copper_ore", 1]], "loot_rolls": 7}
    assert len(roll_loot(template, random.Random(5))) == 7
    assert roll_loot({"loot_table": [["copper_ore", 1]], "loot_rolls": 0}, random.Random(5)) == []


def test_weights_shift_the_distribution():
    """Rare entries must actually be rare -- a drop table that ignores its
    weights would pass every other test in this file."""
    common = {"loot_table": [[NOTHING, 9], ["copper_ore", 1]], "loot_rolls": 1000}
    drops = roll_loot(common, random.Random(7))
    assert 50 < len(drops) < 150, f"expected ~10% drop rate, got {len(drops)/10:.1f}%"
