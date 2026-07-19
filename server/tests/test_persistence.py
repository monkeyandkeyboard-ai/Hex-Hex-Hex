"""Player progress persistence.

The save/load path had no tests, which is uncomfortable for the one piece of
code whose failure silently destroys player progress rather than throwing.
"""
import pathlib

import pytest

from gep import db
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.server import build_player, save_players

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def cfg():
    return ConfigStore(CONFIG_DIR)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the database at a scratch file. db._conn() reads DB_PATH at call
    time, so patching the module attribute is enough."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_players.db")
    return db


def make_player(pid="p1", name="Hero"):
    return Player(
        id=pid, name=name, tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=10, max_mana=10, weapon_id="unarmed",
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
    player.equipment.main_hand = "unarmed"

    temp_db.save_player(player)
    loaded = temp_db.load_player("p1")

    stacks = {v["item_id"]: v["quantity"] for v in loaded["inventory"].values() if v}
    assert stacks == {"copper_ore": 7, "iron_ore": 3}
    assert loaded["equipment"]["main_hand"] == "unarmed"


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


# --- Vitality persistence -------------------------------------------------
# A character who logs out injured must log back in injured, which is the
# stable baseline healing and regen work will be measured against.


def test_injured_player_reloads_still_injured(temp_db, cfg):
    player = make_player()
    player.hp = 137.5
    player.mana = 12.25
    temp_db.save_player(player)

    restored = build_player("p1", "Hero", temp_db.load_player("p1"), cfg)

    assert restored.hp == pytest.approx(137.5)
    assert restored.mana == pytest.approx(12.25)


def test_new_character_starts_at_full(temp_db, cfg):
    fresh = build_player("nobody", "Newbie", None, cfg)
    assert fresh.hp == fresh.max_hp
    assert fresh.mana == fresh.max_mana


def test_character_predating_vitality_columns_starts_full(temp_db, cfg):
    """A row saved before hp/mana existed reads back as NULL, which must mean
    'full', not 'zero'."""
    player = make_player()
    temp_db.save_player(player)
    saved = temp_db.load_player("p1")
    saved["hp"] = saved["mana"] = None          # simulate the legacy row

    restored = build_player("p1", "Hero", saved, cfg)
    assert restored.hp == restored.max_hp
    assert restored.mana == restored.max_mana


def test_max_hp_follows_constitution(temp_db, cfg):
    """Levelling constitution must actually raise the ceiling. This was
    previously computed from a hardcoded level 1 before the save was read."""
    weak = make_player("weak")
    weak.skills.combat["constitution"] = 1
    strong = make_player("strong")
    strong.skills.combat["constitution"] = 12
    temp_db.save_player(weak)
    temp_db.save_player(strong)

    a = build_player("weak", "A", temp_db.load_player("weak"), cfg)
    b = build_player("strong", "B", temp_db.load_player("strong"), cfg)

    assert b.max_hp > a.max_hp
    assert b.max_hp == pytest.approx(
        cfg.stat_scaling["hp_base"] + 12 * cfg.stat_scaling["hp_per_con"])


def test_saved_hp_above_current_max_is_clamped(temp_db, cfg):
    """The ceiling can move down between sessions (a stat_scaling rebalance),
    and current HP must not be left above it."""
    player = make_player()
    player.hp = 999_999
    temp_db.save_player(player)

    restored = build_player("p1", "Hero", temp_db.load_player("p1"), cfg)
    assert restored.hp == restored.max_hp


def test_non_positive_saved_hp_restores_to_full(temp_db, cfg):
    """Nothing damages players yet and there is no respawn flow, so loading a
    character at 0 hp would strand them permanently."""
    player = make_player()
    player.hp = 0
    temp_db.save_player(player)

    restored = build_player("p1", "Hero", temp_db.load_player("p1"), cfg)
    assert restored.hp == restored.max_hp


def test_vitality_survives_the_full_save_load_cycle_with_xp(temp_db, cfg):
    """The whole record moves together: an injured, experienced character."""
    player = make_player()
    player.skills.combat.update({"constitution": 5, "precision": 13})
    player.skills.combat_xp.update({"precision": 3330.05})
    player.hp = 88.0
    temp_db.save_player(player)

    restored = build_player("p1", "Hero", temp_db.load_player("p1"), cfg)

    assert restored.hp == pytest.approx(88.0)
    assert restored.max_hp == pytest.approx(
        cfg.stat_scaling["hp_base"] + 5 * cfg.stat_scaling["hp_per_con"])
    assert restored.skills.combat["precision"] == 13
    assert restored.skills.combat_xp["precision"] == pytest.approx(3330.05)
