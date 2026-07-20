"""Auto-combat: engage once, the server keeps swinging, movement breaks it."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.stats import xp_for_level
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.systems import combat_system, movement
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def make_player(tile=(0, 0)):
    player = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=10_000, max_hp=10_000, mana=0, max_mana=0, weapon_id="unarmed", skills=Skills(),
    )
    # Hit hard and often so swings land and the loop is observable.
    player.skills.combat.update({"strength": 400, "precision": 400, "dexterity": 400})
    return player


def setup(store, floor, player, tanky=True):
    """Wire combat + movement exactly as server.py does."""
    # Back-fill XP to match the levels make_player set. Levels are DERIVED
    # from cumulative XP on every award (gep/xp.py), so a level set without
    # its XP is a time bomb: the first hit that awards XP recomputes the
    # level from ~0 XP and silently resets 400 back to 1 mid-test. That was
    # a real flake -- when the opening swing rolled low damage instead of
    # one-shotting the rat, the collapsed stats could not finish it in the
    # remaining budget (~1 damage a hit through a 70% dodge rate).
    for skill, level in player.skills.combat.items():
        player.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)

    monster = roll_monster("m1", store.monsters["cave_rat"], store.stat_scaling)
    monster.tile = (0, 1)
    monster.floor_number = 5
    if tanky:
        monster.hp = monster.max_hp = 10_000_000   # survives a long engagement
    floor.monsters["m1"] = monster
    floor.players[player.id] = player

    engine = TickEngine()
    break_engagement = combat_system.register(
        engine, floor,
        weapons=store.weapons, monsters_cfg=store.monsters,
        combat_constants=store.combat_constants, xp_rates=store.xp_rates,
        xp_table=store.xp_table, stat_scaling=store.stat_scaling,
        rewards=store.rewards, items=store.items,
        weapon_classes=store.weapon_classes, power_scaling=store.power_scaling,
    )
    movement.register(engine, floor, on_move=break_engagement)
    return engine, monster


def attack(engine, target="m1"):
    return engine.step([{"intent_type": "attack", "player_id": "p1", "target_id": target}])


def swings_over(engine, ticks):
    total = 0
    for _ in range(ticks):
        total += len([e for e in engine.step([]).events if e["type"] == "combat_result"])
    return total


def test_one_attack_intent_keeps_swinging_without_further_input(store):
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player)

    attack(engine)
    assert player.combat_target == "m1"

    # No further intents: the server must keep attacking on its own.
    assert swings_over(engine, 20) > 1


def test_swings_follow_weapon_speed_not_one_per_tick(store):
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player)
    speed = store.weapons["unarmed"]["speed_ticks"]

    attack(engine)
    ticks = 40
    swings = swings_over(engine, ticks)

    expected = ticks // speed
    assert abs(swings - expected) <= 1, (
        f"expected ~{expected} swings at speed {speed}, got {swings}"
    )


def test_movement_command_breaks_the_engagement(store):
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player)

    attack(engine)
    assert player.combat_target == "m1"

    result = engine.step([{"intent_type": "move-to-tile", "player_id": "p1",
                           "target_q": 3, "target_r": 3}])
    assert player.combat_target is None
    assert any(e["type"] == "engagement_ended" and e["reason"] == "moved"
               for e in result.events)

    # And stays broken -- no stale queued swing lands afterwards.
    assert swings_over(engine, 20) == 0


def test_engagement_ends_when_the_target_dies(store):
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player, tanky=False)

    attack(engine)
    for _ in range(60):
        engine.step([])
        if not monster.alive:
            break

    assert not monster.alive
    assert player.combat_target is None
    assert swings_over(engine, 10) == 0, "kept swinging at a corpse"


def test_attacker_turns_to_face_the_target(store):
    floor = make_floor()
    player = make_player(tile=(0, 0))
    engine, monster = setup(store, floor, player)
    player.facing = "up"                      # looking the wrong way

    monster.tile = (0, 1)                     # straight below
    result = attack(engine)

    assert player.facing == "down"
    turn = [e for e in result.events
            if e["type"] == "position_update" and e["player_id"] == "p1"]
    assert turn, "the turn was not broadcast"
    assert turn[0]["facing"] == "down"
    assert turn[0]["tile"] == [0, 0], "a turn must not report a move"


def test_facing_tracks_a_target_that_moves_away(store):
    """Facing must snap to the best of the six directions at any distance,
    not just for adjacent targets."""
    floor = make_floor()
    player = make_player(tile=(0, 0))
    engine, monster = setup(store, floor, player)

    attack(engine)
    monster.tile = (0, 6)          # far below
    engine.step([])
    assert player.facing == "down"

    monster.tile = (5, -3)         # far to the upper right
    for _ in range(10):
        engine.step([])
    assert player.facing == "right-up"


def test_retargeting_drops_the_previous_engagement(store):
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player)

    second = roll_monster("m2", store.monsters["cave_rat"], store.stat_scaling)
    second.tile = (1, 0)
    second.hp = second.max_hp = 10_000_000
    floor.monsters["m2"] = second

    attack(engine, "m1")
    attack(engine, "m2")
    assert player.combat_target == "m2"

    # Only the new target should be taking hits.
    hp1_before, hp2_before = monster.hp, second.hp
    swings_over(engine, 30)
    assert monster.hp == hp1_before, "stale engagement still swinging at old target"
    assert second.hp < hp2_before


def test_attacking_while_on_cooldown_still_engages(store):
    """Clicking a target mid-swing should start the engagement, not error."""
    floor = make_floor()
    player = make_player()
    engine, monster = setup(store, floor, player)

    attack(engine)                       # consumes the weapon, sets cooldown
    result = attack(engine)              # immediately again, still cooling down

    assert player.combat_target == "m1"
    assert not [e for e in result.events if e["type"] == "error"]
    assert swings_over(engine, 20) > 0
