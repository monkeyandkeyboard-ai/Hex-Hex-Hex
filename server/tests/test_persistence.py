"""Player progress persistence.

The save/load path had no tests, which is uncomfortable for the one piece of
code whose failure silently destroys player progress rather than throwing.
"""
import pytest

from gep import db
from gep.entities import Player, Skills
from gep.server import save_players


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database at a scratch file. db._conn() reads DB_PATH at call
    time, so patching the module attribute is enough."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_players.db")
    return db


def make_player(pid="p1", name="Hero"):
    return Player(
        id=pid, name=name, tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=10, max_mana=10, weapon_id="fists",
        skills=Skills(),
    )


def test_combat_levels_and_xp_survive_a_round_trip(temp_db):
    player = make_player()
    player.skills.combat.update({"strength": 9, "precision": 7, "constitution": 2})
    player.skills.combat_xp.update({"strength": 377.66, "precision": 188.83, "constitution": 1.2})

    temp_db.save_player(player)
    loaded = temp_db.load_player("p1")

    assert loaded["combat_levels"]["strength"] == 9
    assert loaded["combat_levels"]["precision"] == 7
    assert loaded["combat_levels"]["constitution"] == 2
    # XP is a float and must not be rounded on the way through.
    assert loaded["combat_xp"]["strength"] == pytest.approx(377.66)
    assert loaded["combat_xp"]["constitution"] == pytest.approx(1.2)


def test_non_combat_levels_and_xp_survive_a_round_trip(temp_db):
    player = make_player()
    player.skills.non_combat = {"mineralogy": 4, "smithing": 2}
    player.skills.non_combat_xp = {"mineralogy": 91.5, "smithing": 12.25}

    temp_db.save_player(player)
    loaded = temp_db.load_player("p1")

    assert loaded["non_combat_levels"] == {"mineralogy": 4, "smithing": 2}
    assert loaded["non_combat_xp"]["mineralogy"] == pytest.approx(91.5)


def test_inventory_and_equipment_survive_a_round_trip(temp_db):
    player = make_player()
    player.add_item("copper_ore", 7)
    player.add_item("iron_ore", 3)
    player.equipment.main_hand = "fists"

    temp_db.save_player(player)
    loaded = temp_db.load_player("p1")

    stacks = {v["item_id"]: v["quantity"] for v in loaded["inventory"].values() if v}
    assert stacks == {"copper_ore": 7, "iron_ore": 3}
    assert loaded["equipment"]["main_hand"] == "fists"


def test_saving_twice_updates_rather_than_duplicating(temp_db):
    player = make_player()
    player.skills.combat["strength"] = 3
    temp_db.save_player(player)

    player.skills.combat["strength"] = 11
    temp_db.save_player(player)

    assert temp_db.load_player("p1")["combat_levels"]["strength"] == 11


def test_unknown_player_loads_as_none(temp_db):
    assert temp_db.load_player("never-existed") is None


def test_save_players_reports_count(temp_db):
    players = [make_player("a"), make_player("b"), make_player("c")]
    assert save_players(players) == 3
    assert temp_db.load_player("b") is not None


def test_one_bad_save_does_not_abandon_the_others(temp_db):
    """Autosave runs on the tick loop. A single broken player must not stop
    the rest from being written, nor raise into the loop."""
    good_one, good_two = make_player("good1"), make_player("good2")
    broken = make_player("broken")
    broken.inventory = {0: {"item_id": object()}}   # not JSON-serialisable

    saved = save_players([good_one, broken, good_two])

    assert saved == 2
    assert temp_db.load_player("good1") is not None
    assert temp_db.load_player("good2") is not None
    assert temp_db.load_player("broken") is None
