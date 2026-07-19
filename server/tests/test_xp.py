import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.xp import award_xp, award_xp_block, current_level

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_player():
    skills = Skills()
    skills.non_combat["mineralogy"] = 1
    skills.non_combat_xp["mineralogy"] = 0.0
    return Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=1000, max_hp=1000, mana=50, max_mana=50, weapon_id="unarmed", skills=skills,
    )


def test_level_1_at_zero_xp(store):
    assert current_level(0, store.xp_table) == 1


def test_level_2_at_xp_threshold(store):
    xp_for_2 = store.xp_table["2"]
    assert current_level(xp_for_2, store.xp_table) == 2
    assert current_level(xp_for_2 - 1, store.xp_table) == 1


def test_level_99_reached(store):
    xp_for_99 = store.xp_table["99"]
    assert current_level(xp_for_99, store.xp_table) == 99


def test_award_xp_increases_stored_xp(store):
    player = make_player()
    award_xp(player, "strength", 50.0, store.xp_table)
    assert player.skills.combat_xp["strength"] == 50.0


def test_award_xp_raises_level_when_threshold_crossed(store):
    player = make_player()
    xp_needed = store.xp_table["2"]
    events = award_xp(player, "strength", float(xp_needed), store.xp_table)
    assert any(e["type"] == "level_up" and e["skill"] == "strength" for e in events)
    assert player.skills.combat["strength"] == 2


def test_no_level_up_event_below_threshold(store):
    player = make_player()
    events = award_xp(player, "strength", 1.0, store.xp_table)
    assert not any(e["type"] == "level_up" for e in events)


def test_award_xp_non_combat_skill(store):
    player = make_player()
    xp_needed = store.xp_table["2"]
    events = award_xp(player, "mineralogy", float(xp_needed), store.xp_table)
    assert player.skills.non_combat["mineralogy"] == 2
    assert any(e["type"] == "level_up" and e["skill"] == "mineralogy" for e in events)


def test_award_xp_unknown_skill_initialises_it(store):
    player = make_player()
    events = award_xp(player, "aboriculture", 1.0, store.xp_table)
    assert player.skills.non_combat_xp.get("aboriculture") == 1.0


def test_award_xp_block_awards_multiple_skills(store):
    player = make_player()
    block = {"mineralogy": 10, "strength": 2}
    award_xp_block(player, block, store.xp_table)
    assert player.skills.non_combat_xp["mineralogy"] == 10
    assert player.skills.combat_xp["strength"] == 2


def test_level_up_event_has_correct_old_and_new_level(store):
    player = make_player()
    xp_for_3 = store.xp_table["3"]
    events = award_xp(player, "dexterity", float(xp_for_3), store.xp_table)
    level_ups = [e for e in events if e["type"] == "level_up"]
    assert len(level_ups) == 1
    assert level_ups[0]["old_level"] == 1
    assert level_ups[0]["new_level"] == 3
