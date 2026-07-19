"""Aggro behaviour, and the isolation between combat and behaviour.

The isolation tests matter as much as the behaviour ones: the whole point of
the callback seam is that damage tuning and pathfinding tuning cannot break
each other, and that is only true as long as neither module reaches into the
other. These tests fail loudly if someone wires them together directly.
"""
import ast
import pathlib
import random

from gep.actions import CLEAR_THREAT
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.hexgrid import hex_distance
from gep.systems import monster_ai
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}

# Step every tick in both modes so behaviour is deterministic to assert on.
ALWAYS = {"cave_rat": {"movement": {
    "wander_interval_ticks": 1, "wander_chance": 1.0, "pursue_interval_ticks": 1,
}}}


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def add_monster(floor, tile=(0, 0), mid="m1"):
    cfg = ConfigStore(CONFIG_DIR)
    monster = roll_monster(mid, cfg.monsters["cave_rat"], cfg.stat_scaling)
    monster.tile = tile
    monster.floor_number = 5
    floor.monsters[mid] = monster
    return monster


def add_player(floor, tile=(0, 5), pid="p1"):
    player = Player(
        id=pid, name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=100, max_hp=100, mana=0, max_mana=0, weapon_id="fists", skills=Skills(),
    )
    floor.players[pid] = player
    return player


def run(engine, ticks):
    events = []
    for _ in range(ticks):
        events.extend(engine.step([]).events)
    return events


def test_threat_slot_starts_empty_and_fills_on_notify():
    floor = make_floor()
    monster = add_monster(floor)
    notify = monster_ai.register(TickEngine(), floor, ALWAYS, rng=random.Random(1))

    assert monster.threat_target is None
    notify(monster, "p1")
    assert monster.threat_target == "p1"


def test_monster_pursues_until_adjacent_then_stops_and_faces_target():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    player = add_player(floor, tile=(0, 6))
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(2))

    notify(monster, "p1")
    run(engine, 12)

    # Closed the distance and stopped adjacent -- never onto the player's tile.
    assert hex_distance(monster.tile, player.tile) == 1
    assert monster.tile != player.tile
    # Held position while adjacent, still looking at the target.
    assert monster.facing == "down"  # (0,5) -> (0,6) is straight down

    before = monster.tile
    run(engine, 5)
    assert monster.tile == before, "monster should hold once adjacent"


def test_turning_in_place_is_broadcast():
    """An adjacent monster turning to track its target is a state change the
    client must hear about; otherwise server and client disagree on which
    sprite frame to draw until the monster next moves.
    """
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    player = add_player(floor, tile=(0, 1))   # already adjacent, monster faces "down"
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(9))
    notify(monster, "p1")
    run(engine, 2)
    assert monster.facing == "down"

    # Player circles to the opposite side without the monster moving.
    player.tile = (0, -1)
    events = [e for e in run(engine, 2) if e["type"] == "monster_moved"]

    assert monster.facing == "up"
    assert events, "turn-in-place emitted no event"
    assert events[-1]["facing"] == "up"
    assert events[-1]["tile"] == [0, 0], "a turn must not report a move"


def test_pursuit_retargets_as_the_player_moves():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    player = add_player(floor, tile=(0, 4))
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(3))
    notify(monster, "p1")

    run(engine, 2)
    # Player runs; the monster must follow the new position, not the old one.
    player.tile = (0, -6)
    run(engine, 20)

    assert hex_distance(monster.tile, player.tile) == 1


def test_threat_clears_when_target_leaves_and_monster_resumes_wandering():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    add_player(floor, tile=(0, 4))
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(4))
    notify(monster, "p1")
    run(engine, 3)

    del floor.players["p1"]          # left the floor
    run(engine, 1)
    assert monster.threat_target is None

    # Wandering again rather than frozen.
    start = monster.tile
    run(engine, 8)
    assert monster.tile != start


def test_dead_monster_does_not_pursue_but_resumes_after_respawn():
    floor = make_floor()
    monster = add_monster(floor, tile=(0, 0))
    add_player(floor, tile=(0, 6))
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(5))
    notify(monster, "p1")

    monster.alive = False
    start = monster.tile
    run(engine, 5)
    assert monster.tile == start

    monster.alive = True
    run(engine, 5)
    assert monster.tile != start


def test_notify_threat_ignores_entities_without_a_threat_slot():
    """Combat calls this for whatever it damaged; behaviour must not care."""
    floor = make_floor()
    notify = monster_ai.register(TickEngine(), floor, ALWAYS, rng=random.Random(6))

    player = add_player(floor)          # players have no threat slot
    notify(player, "p2")                # must not raise or invent an attribute
    assert not hasattr(player, "threat_target")

    notify(add_monster(floor), None)    # no attacker id -> no acquisition
    assert floor.monsters["m1"].threat_target is None


GEP = pathlib.Path(__file__).resolve().parents[1] / "gep"


def _module_facts(relative_path):
    """Imported module names and attribute names used, via AST -- so comments
    and docstrings explaining the seam don't count as coupling.
    """
    tree = ast.parse((GEP / relative_path).read_text())
    imports, attributes = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
            imports.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
    return imports, attributes


def test_combat_never_reaches_into_behaviour():
    """Combat may report that damage landed; it may not decide what that
    means. If it grows an import of the behaviour system or writes the threat
    slot directly, damage tuning and pathfinding stop being independently
    tunable -- the property this arrangement exists to protect.
    """
    for module in ("combat.py", "systems/combat_system.py"):
        imports, attributes = _module_facts(module)
        assert not any("monster_ai" in name for name in imports), \
            f"{module} imports the behaviour system"
        assert "threat_target" not in attributes, \
            f"{module} writes the threat slot directly instead of via on_threat"


def test_behaviour_never_reaches_into_combat():
    imports, attributes = _module_facts("systems/monster_ai.py")
    assert not any("combat" in name for name in imports), \
        "behaviour imports combat"
    for damage_concept in ("damage_min", "damage_max", "roll_damage", "hp"):
        assert damage_concept not in attributes, \
            f"behaviour touches {damage_concept}: damage belongs to combat"


def test_threat_table_accumulates_per_attacker():
    """Threat is a table now, not a single slot: two attackers both register,
    and the one with more accumulated threat is the one hunted."""
    floor = make_floor()
    monster = add_monster(floor)
    notify = monster_ai.register(TickEngine(), floor, ALWAYS, rng=random.Random(1))

    notify(monster, "p1")
    notify(monster, "p2")
    notify(monster, "p2")

    assert monster.threat_table == {"p1": 1.0, "p2": 2.0}
    assert monster.threat_target == "p2"


def test_defeat_removes_only_the_dead_player_leaving_other_aggro_intact():
    """The regression the old floor-wide sweep could not express: one player
    dying must not wipe the monster's threat on everyone else."""
    floor = make_floor()
    monster = add_monster(floor)
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(1))

    add_player(floor, tile=(0, 5), pid="p1")
    add_player(floor, tile=(0, 6), pid="p2")
    notify(monster, "p1")
    notify(monster, "p2")
    notify(monster, "p2")

    engine.schedule(0, CLEAR_THREAT, {"player_id": "p2"})
    run(engine, 1)

    assert "p2" not in monster.threat_table
    assert monster.threat_target == "p1", "surviving attacker lost aggro"


def test_clear_threat_does_not_scan_monsters_that_never_held_the_player():
    """O(1): the handler touches only monsters in the reverse index, so its
    cost tracks the number of actual holders, not the floor's population."""
    floor = make_floor()
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(1))

    holder = add_monster(floor, tile=(0, 0), mid="holder")
    bystanders = [add_monster(floor, tile=(0, i), mid=f"m{i}") for i in range(1, 40)]
    add_player(floor, tile=(0, 5), pid="p1")
    notify(holder, "p1")

    visited = []

    class CountingDict(dict):
        def get(self, key, *default):
            visited.append(key)
            return super().get(key, *default)

    floor.monsters = CountingDict(floor.monsters)
    engine.schedule(0, CLEAR_THREAT, {"player_id": "p1"})
    run(engine, 1)

    assert visited == ["holder"], f"swept extra monsters: {visited}"
    assert holder.threat_table == {}
    assert all(b.threat_table == {} for b in bystanders)


def test_clearing_an_unknown_player_is_a_noop():
    floor = make_floor()
    monster = add_monster(floor)
    engine = TickEngine()
    notify = monster_ai.register(engine, floor, ALWAYS, rng=random.Random(1))
    add_player(floor, tile=(0, 5), pid="p1")
    notify(monster, "p1")

    engine.schedule(0, CLEAR_THREAT, {"player_id": "ghost"})
    run(engine, 1)

    assert monster.threat_target == "p1"
