"""Cross-domain conversion: outer loops feeding the stat pipeline.

The most important tests here are the refusals. This seam exists to let
non-combat progression grant power *without* becoming a prerequisite for
combat, and that guarantee is only real while the config that would break it
fails to load.
"""
import pathlib
import random

import pytest

from gep import crossdomain
from gep.config_loader import ITEM_STATS, ConfigError, ConfigStore
from gep.crossdomain import (
    CONSUMED_UTILITY_STATS,
    UTILITY_STATS,
    ConversionError,
    build_block,
    validate_conversions,
)
from gep.entities import Player, Skills
from gep.statblock import StatBlock

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture(scope="module")
def store():
    return ConfigStore(CONFIG_DIR)


def _player(**non_combat):
    player = Player(
        id="p1", name="T", tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=0, max_mana=0, weapon_id="unarmed",
        skills=Skills(),
    )
    player.skills.non_combat = dict(non_combat)
    return player


def _conv(source, target, cls="increased", per_level=1.0):
    return {"source": source, "target": target, "class": cls, "per_level": per_level}


SKILL = {"kind": "non_combat_skill", "name": "mineralogy"}
DEPTH = {"kind": "floor_depth"}


# --- building --------------------------------------------------------------

def test_skill_level_scales_the_coefficient():
    block = build_block([_conv(SKILL, "gather_yield", per_level=0.5)],
                        {"mineralogy": 40}, floor_number=1)
    # 40 levels * 0.5 = +20% increased
    assert block.resolve("gather_yield", base=100) == pytest.approx(120.0)


def test_floor_depth_is_a_source():
    block = build_block([_conv(DEPTH, "item_rarity", per_level=0.25)],
                        {}, floor_number=200)
    assert block.resolve("item_rarity", base=1.0) == pytest.approx(1.5)


def test_an_unlevelled_skill_contributes_nothing():
    """Never having touched mineralogy is the normal case at level 1, not an
    error, and must not be one."""
    block = build_block([_conv(SKILL, "gather_yield")], {}, floor_number=1)
    assert block.resolve("gather_yield", base=100) == 100


def test_conversions_join_the_same_pools_as_affixes():
    """A conversion is an ordinary modifier with an unusual source, so it
    sums with an item's increase rather than applying after it."""
    player = _player(mineralogy=20)
    player.stats.globals.add("gather_yield_percent", 50)

    value = crossdomain.resolve(
        player, 1, [_conv(SKILL, "gather_yield", per_level=1.0)],
        "gather_yield", base=100,
    )
    # 20 levels * 1.0 = +20%, plus the item's +50%, summed to +70%.
    assert value == pytest.approx(170.0)


def test_more_class_stays_independent():
    block = build_block([_conv(SKILL, "gather_yield", cls="more", per_level=1.0)],
                        {"mineralogy": 20}, floor_number=1)
    other = StatBlock()
    other.add("gather_yield_more", 20)
    block.merge(other)
    assert block.resolve("gather_yield", base=100) == pytest.approx(100 * 1.2 * 1.2)


# --- the refusals ----------------------------------------------------------

@pytest.mark.parametrize("stat", sorted(ITEM_STATS))
def test_no_conversion_may_target_a_combat_stat(stat):
    """The constraint the seam exists to enforce, checked against every
    combat stat rather than a sample -- adding a seventh must not open a
    hole."""
    with pytest.raises(ConversionError, match="combat stat"):
        validate_conversions([_conv(SKILL, stat)], ITEM_STATS)


def test_declared_but_unconsumed_targets_are_refused():
    """`craft_quality` has no crafting system. Wiring power into it would
    grant nothing, which is the dead-config failure this replaced."""
    unconsumed = [s for s, c in UTILITY_STATS.items() if c is None]
    assert unconsumed, "test is vacuous if every declared stat has a consumer"
    for stat in unconsumed:
        with pytest.raises(ConversionError, match="nothing reads it"):
            validate_conversions([_conv(SKILL, stat)], ITEM_STATS)


def test_unknown_target_is_refused():
    with pytest.raises(ConversionError, match="unknown target"):
        validate_conversions([_conv(SKILL, "gather_yeild")], ITEM_STATS)


def test_unknown_class_is_refused():
    with pytest.raises(ConversionError, match="'class' must be"):
        validate_conversions([_conv(SKILL, "gather_yield", cls="multiplied")], ITEM_STATS)


def test_unknown_source_kind_is_refused():
    with pytest.raises(ConversionError, match="source.kind"):
        validate_conversions([_conv({"kind": "vibes"}, "gather_yield")], ITEM_STATS)


def test_missing_field_is_refused():
    with pytest.raises(ConversionError, match="missing 'per_level'"):
        validate_conversions(
            [{"source": SKILL, "target": "gather_yield", "class": "flat"}], ITEM_STATS
        )


def test_misspelled_skill_name_is_refused_at_load(store, tmp_path):
    """A misspelled skill would contribute zero forever -- a conversion that
    silently does nothing, which is exactly what this seam replaced."""
    import json
    import shutil

    shutil.copytree(CONFIG_DIR, tmp_path / "config")
    path = tmp_path / "config" / "cross_domain.json"
    data = json.loads(path.read_text())
    data["conversions"] = [_conv({"kind": "non_combat_skill", "name": "minerology"},
                                 "gather_yield")]
    path.write_text(json.dumps(data))

    with pytest.raises(ConfigError, match="unknown non-combat skill"):
        ConfigStore(tmp_path / "config")


# --- the shipped config ----------------------------------------------------

def test_shipped_conversions_all_target_consumed_utility_stats(store):
    assert store.conversions, "config ships no conversions; the seam is inert"
    for conversion in store.conversions:
        assert conversion["target"] in CONSUMED_UTILITY_STATS


def test_influences_block_is_gone(store):
    """It encoded the rejected full-conversion model and nothing read it."""
    for skill, body in store.skills["non_combat_skills"].items():
        assert "influences" not in body, f"{skill} still declares influences"


# --- end to end ------------------------------------------------------------

def test_a_master_miner_out_gathers_a_novice(store):
    """The seam, observed through the system that reads it."""
    from gep.floor_state import FloorState
    from gep.floorgen import generate_floor
    from gep.systems import gathering
    from gep.tick import TickEngine

    ruleset = {"radius": 6, "resource_spawn_chance": 0.0,
               "resource_weights": [["iron_ore", 1]], "monster_spawn_count": 0,
               "monster_weights": [["cave_rat", 1]]}

    def one_gather(mineralogy_level, seed):
        floor = FloorState.from_layout(generate_floor("tower-a", 3, "s", ruleset))
        player = _player(mineralogy=mineralogy_level)
        # Levels derive from XP on every award (gep/xp.py), so back-fill the
        # XP behind the level or the gather's own XP award would reset a
        # bare level-99 to 1. Harmless today only because yield resolves
        # before the award -- the same landmine that made
        # test_auto_combat flaky, defused rather than left to timing.
        from gep.stats import xp_for_level
        player.skills.non_combat_xp["mineralogy"] = xp_for_level(
            mineralogy_level, store.xp_table)
        floor.players["p1"] = player
        floor.resource_nodes[(0, 0)] = "iron_ore"
        engine = TickEngine()
        gathering.register(engine, floor, store.resources, store.xp_table,
                           store.conversions)
        random.seed(seed)
        engine.step([{"intent_type": "gather-node", "player_id": "p1",
                      "tile_q": 0, "tile_r": 0}])
        for _ in range(60):
            engine.step([])
            got = [s for s in player.inventory_snapshot() if s]
            if got:
                return got[0]["quantity"]
        raise AssertionError("gather never completed")

    # Summed over many gathers rather than compared one-to-one. iron_ore
    # yields 1-2, so a single +49.5% roll often rounds back to where it
    # started -- a per-gather assertion would pass or fail on the base roll,
    # not on the conversion.
    seeds = range(40)
    novice = sum(one_gather(1, s) for s in seeds)
    master = sum(one_gather(99, s) for s in seeds)
    assert master > novice, f"master {master} did not out-gather novice {novice}"


def test_rarity_cannot_push_a_guaranteed_drop_past_certainty(store):
    """A chest at drop_chance 1.0 must not start rolling twice."""
    table = {"kind": "equipment", "drop_chance": 1.0, "min_tier": 1, "max_tier": 1}
    rolled = store.rewards._roll_equipment(table, 3, random.Random(1), rarity=50.0)
    assert len(rolled) == 3
