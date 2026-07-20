"""Floor transitions via FloorManager, using real generated floors."""
import pathlib

from gep.config_loader import ConfigStore
from gep.floor_manager import FloorManager
from gep.entities import Player, Skills
from gep.server import build_floor_state

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _manager():
    return FloorManager(lambda n, ocf, rel: build_floor_state(n, cfg, ocf, rel))


def _player(pid="p1"):
    return Player(
        id=pid, name=pid, tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=10, max_mana=10, weapon_id="unarmed", skills=Skills(),
    )


def test_add_player_lands_on_floor_1():
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    assert m.player_floor["p1"] == 1
    assert "p1" in m.floors[1][0].players


def test_going_up_moves_player_and_lands_on_down_exit():
    m = _manager()
    p = _player()
    m.add_player(p, 1)

    new_floor = m.change_floor("p1", "up")
    assert new_floor == 2
    assert m.player_floor["p1"] == 2
    # Removed from floor 1, present on floor 2.
    assert "p1" not in m.floors[1][0].players
    assert "p1" in m.floors[2][0].players
    # Arrived on floor 2's down-stairs (the way back down).
    assert p.tile == m.floors[2][0].layout.down_exit
    assert "p1" in m.pending_snapshots


def test_going_down_returns_to_up_exit():
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    m.change_floor("p1", "up")      # now on floor 2
    m.pending_snapshots.clear()

    back = m.change_floor("p1", "down")
    assert back == 1
    assert p.tile == m.floors[1][0].layout.up_exit


def test_cannot_go_below_floor_1():
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    assert m.change_floor("p1", "down") is None
    assert m.player_floor["p1"] == 1


def test_travel_is_reversible():
    """Up then down must return you to the tile you left from -- the whole
    point of the reciprocal arrival rule."""
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    p.tile = m.floors[1][0].layout.up_exit

    m.change_floor("p1", "up")
    assert p.tile == m.floors[2][0].layout.down_exit
    m.change_floor("p1", "down")
    assert p.tile == m.floors[1][0].layout.up_exit
    assert p.floor_number == 1


def test_move_to_floor_is_absolute_not_a_step():
    """Respawn sends a player to an anchor floor directly. Routing that
    through the stairs mover would read the floor number as a direction and
    move them one floor instead."""
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    m.change_floor("p1", "up")
    m.change_floor("p1", "up")
    assert p.floor_number == 3

    assert m.move_to_floor("p1", 1, (0, 0)) == 1
    assert p.floor_number == 1
    assert p.tile == (0, 0)
    assert "p1" not in m.floors[3][0].players
    assert "p1" in m.floors[1][0].players


def test_relocation_carries_player_state():
    """Entity state must survive the handoff -- the player object moves, it is
    not rebuilt from defaults."""
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    p.hp = 37.0
    skill = next(iter(p.skills.combat_xp))
    p.skills.combat_xp[skill] = 1234.0

    m.change_floor("p1", "up")
    moved = m.floors[2][0].players["p1"]
    assert moved is p
    assert moved.hp == 37.0
    assert moved.skills.combat_xp[skill] == 1234.0


def test_floor_1_entrance_is_never_occupied():
    """(0, 0) is where players arrive on floor 1; nothing may spawn there."""
    floor = _manager().get_or_build(1)[0]
    assert all(tuple(mon.tile) != (0, 0) for mon in floor.monsters.values())


def test_floors_are_built_lazily():
    m = _manager()
    p = _player()
    m.add_player(p, 1)
    assert set(m.floors) == {1}
    m.change_floor("p1", "up")
    assert set(m.floors) == {1, 2}
