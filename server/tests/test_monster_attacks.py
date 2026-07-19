"""Monsters striking players, and the player defeat lifecycle."""
import pathlib

import pytest

from gep.actions import MONSTER_STRIKE, PLAYER_DEFEATED
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.systems import combat_system, monster_ai, respawn
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 8,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}

# Think every tick so the strike request is prompt and deterministic.
ALWAYS = {"cave_rat": {
    "movement": {"wander_interval_ticks": 1, "wander_chance": 1.0,
                 "pursue_interval_ticks": 1},
    "combat": {"damage_min": 40, "damage_max": 40, "speed_ticks": 4,
               "attack_range_tiles": 1},
}}


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


def make_floor():
    return FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))


def make_player(tile=(0, 0), hp=500.0):
    player = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=hp, max_hp=500.0, mana=0.0, max_mana=50.0, weapon_id="unarmed",
        skills=Skills(),
    )
    player.skills.combat["dexterity"] = 0     # never dodge, so hits land
    return player


def add_monster(store, floor, tile, mid="m1"):
    monster = roll_monster(mid, store.monsters["cave_rat"], store.stat_scaling)
    monster.tile = tile
    monster.floor_number = 5
    monster.damage_min = monster.damage_max = 40.0
    monster.speed_ticks = 4
    monster.stats["precision"] = 500          # never miss
    floor.monsters[mid] = monster
    return monster


def setup(store, floor, saves=None, cfg_override=ALWAYS):
    engine = TickEngine()
    respawn.register(engine, floor,
                     save_player=(saves.append if saves is not None else None))
    notify = monster_ai.register(engine, floor, cfg_override)
    combat_system.register(
        engine, floor, weapons=store.weapons, monsters_cfg=cfg_override,
        combat_constants=store.combat_constants, xp_rates=store.xp_rates,
        xp_table=store.xp_table, stat_scaling=store.stat_scaling,
        rewards=store.rewards, items=store.items,
        weapon_classes=store.weapon_classes, power_scaling=store.power_scaling,
        on_threat=notify,
    )
    return engine, notify


def run(engine, ticks):
    events = []
    for _ in range(ticks):
        events.extend(engine.step([]).events)
    return events


def test_adjacent_monster_damages_its_target(store):
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(0, 1))
    engine, notify = setup(store, floor)

    notify(monster, "p1")
    start_hp = player.hp
    run(engine, 6)

    assert player.hp < start_hp, "an adjacent monster never landed a hit"


def test_monster_out_of_range_does_not_strike(store):
    """The strike gate is range, not merely having a target."""
    floor = make_floor()
    player = make_player(tile=(0, 0))
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(0, 6))
    engine, notify = setup(store, floor)
    notify(monster, "p1")

    # One tick: the monster is still far away and can only have stepped once.
    engine.step([])
    assert player.hp == 500.0


def test_strikes_obey_the_monster_cooldown(store):
    """Behaviour asks every tick; pacing is enforced by combat, so the swing
    rate follows speed_ticks rather than the request rate."""
    floor = make_floor()
    player = make_player(tile=(0, 0))
    # Unkillable for the duration: this measures swing pacing, and a player
    # who dies mid-test stops being a target and truncates the count.
    player.hp = player.max_hp = 1e9
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(0, 1))
    engine, notify = setup(store, floor)
    notify(monster, "p1")

    ticks = 20
    results = [e for e in run(engine, ticks)
               if e["type"] == "combat_result" and e["attacker"] == "m1"]

    expected = ticks // monster.speed_ticks
    assert abs(len(results) - expected) <= 1, (
        f"expected ~{expected} swings at speed {monster.speed_ticks}, got {len(results)}")


def test_defeated_player_returns_to_anchor_restored(store):
    floor = make_floor()
    player = make_player(tile=(3, 3), hp=45.0)   # one 40-damage hit from death
    player.spawn_tile = (0, 0)
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(3, 4))
    saves = []
    engine, notify = setup(store, floor, saves=saves)
    notify(monster, "p1")

    events = run(engine, 12)

    died = [e for e in events if e["type"] == "player_died"]
    assert died, "player never died despite lethal damage"
    assert player.tile == (0, 0), "not returned to the anchor"
    assert player.hp == player.max_hp
    assert player.mana == player.max_mana
    assert player.alive is True
    # Blocking flush happened, so the relocation survives a crash.
    assert saves, "defeat did not force a save"


def test_defeat_clears_aggro_and_stops_auto_combat(store):
    floor = make_floor()
    player = make_player(tile=(3, 3), hp=45.0)
    player.combat_target = "m1"
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(3, 4))
    engine, notify = setup(store, floor)
    notify(monster, "p1")
    assert monster.threat_target == "p1"

    run(engine, 12)

    assert monster.threat_target is None, "monster still hunting a dead player"
    assert player.combat_target is None, "auto-combat survived death"


def test_dead_player_is_never_persisted_as_dead(store):
    """alive is restored before the write, so a crash mid-defeat cannot leave
    a character stuck in a dead state on disk."""
    floor = make_floor()
    player = make_player(tile=(3, 3), hp=45.0)
    floor.players["p1"] = player
    monster = add_monster(store, floor, tile=(3, 4))
    saved_states = []

    engine = TickEngine()
    respawn.register(engine, floor,
                     save_player=lambda p: saved_states.append((p.alive, p.hp, p.tile)))
    notify = monster_ai.register(engine, floor, ALWAYS)
    combat_system.register(
        engine, floor, weapons=store.weapons, monsters_cfg=ALWAYS,
        combat_constants=store.combat_constants, xp_rates=store.xp_rates,
        xp_table=store.xp_table, stat_scaling=store.stat_scaling,
        rewards=store.rewards, items=store.items,
        weapon_classes=store.weapon_classes, power_scaling=store.power_scaling,
        on_threat=notify)
    notify(monster, "p1")

    run(engine, 12)

    assert saved_states, "no save captured"
    alive, hp, tile = saved_states[-1]
    assert alive is True
    assert hp == player.max_hp
    assert tile == (0, 0)
