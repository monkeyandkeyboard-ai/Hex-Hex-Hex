"""Per-tick broadcast routing (gep/server.build_broadcasts).

This lived inline in run_server's loop and so was unreachable from any test --
which is how a crash on the very first floor transition shipped. It is a
separate function now specifically so the case below stays covered.
"""
import pathlib

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.floor_manager import FloorManager
from gep.server import build_broadcasts, build_floor_state

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
cfg = ConfigStore(CONFIG_DIR)


def _manager():
    return FloorManager(lambda n, ocf, rel: build_floor_state(n, cfg, ocf, rel))


def _player(pid="p1"):
    return Player(
        id=pid, name=pid, tower_id="tower-a", floor_number=1, tile=(0, 0),
        hp=100, max_hp=100, mana=10, max_mana=10, weapon_id="unarmed", skills=Skills(),
    )


def _step_all(floors):
    """The first half of a tick: step every floor that exists right now."""
    return {n: eng.step([]) for n, (_, eng) in list(floors.items())}


def test_broadcast_routes_a_tick_result_to_the_players_on_that_floor():
    m = _manager()
    m.add_player(_player(), 1)
    results = _step_all(m.floors)

    sent = build_broadcasts(results, m.floors, m.player_floor, ["p1"], cfg)
    assert len(sent) == 1
    recipients, payload = sent[0]
    assert recipients == ["p1"]
    assert "tick" in payload and "events" in payload


def test_floor_built_mid_tick_does_not_crash_the_broadcast():
    """The KeyError: 2 crash.

    Taking the stairs runs inside the step loop and builds the destination
    floor, so `floors` grows between stepping and broadcasting. Iterating
    `floors` here meant looking up a tick result that was never produced.
    """
    m = _manager()
    p = _player()
    m.add_player(p, 1)

    results = _step_all(m.floors)          # only floor 1 exists and is stepped
    m.change_floor("p1", "up")             # use-exit builds floor 2 mid-tick
    assert set(m.floors) == {1, 2}
    assert set(results) == {1}

    sent = build_broadcasts(results, m.floors, m.player_floor, ["p1"], cfg)

    # Must not raise. The player is on floor 2 now, which was never stepped, so
    # there is nothing to broadcast to them this tick.
    assert sent == []


def test_arriving_player_is_covered_by_a_snapshot_not_this_broadcast():
    """Skipping the unstepped floor is only safe because the transition queues
    a full snapshot, which says more than a tick result would."""
    m = _manager()
    m.add_player(_player(), 1)
    _step_all(m.floors)
    m.change_floor("p1", "up")
    assert "p1" in m.pending_snapshots


def test_empty_floors_are_not_broadcast_to():
    """Floor 1 keeps ticking after the player leaves; nobody should be sent
    its results."""
    m = _manager()
    m.add_player(_player(), 1)
    m.change_floor("p1", "up")

    results = _step_all(m.floors)          # both floors exist and are stepped
    sent = build_broadcasts(results, m.floors, m.player_floor, ["p1"], cfg)

    assert len(sent) == 1
    recipients, _ = sent[0]
    assert recipients == ["p1"]
    assert m.player_floor["p1"] == 2


def test_players_on_different_floors_get_different_payloads():
    m = _manager()
    m.add_player(_player("p1"), 1)
    m.add_player(_player("p2"), 1)
    m.change_floor("p1", "up")

    results = _step_all(m.floors)
    sent = build_broadcasts(results, m.floors, m.player_floor, ["p1", "p2"], cfg)

    routed = {tuple(r): payload for r, payload in sent}
    assert ("p1",) in routed and ("p2",) in routed
    assert routed[("p1",)] is not routed[("p2",)]
