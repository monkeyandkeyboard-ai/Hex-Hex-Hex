"""Abilities: known-set derivation, single-target and AoE casts, the gates
that stop a cast, and the hostiles-only target rule."""
import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Equipment, Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import xp_for_level
from gep.systems import abilities
from gep.systems.abilities import known_abilities
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


def make_player(store, tile=(0, 0), **skills):
    player = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=10_000, max_hp=10_000, mana=1000, max_mana=1000,
        weapon_id="unarmed", skills=Skills(),
    )
    # High precision by default so casts actually land -- a cave_rat's evasion
    # otherwise dodges a precision-1 caster ~80% of the time and makes hit
    # assertions flaky. Callers override any skill they care about.
    player.skills.combat["precision"] = 400
    player.skills.combat.update(skills)
    # Back-fill XP for every set level, or the first XP award recomputes the
    # level from ~0 XP and silently resets it (gep/xp.py). See project memory.
    for skill, level in player.skills.combat.items():
        player.skills.combat_xp[skill] = xp_for_level(int(level), store.xp_table)
    player.refresh_stats(store.items)
    return player


def add_monster(store, floor, tile, mid, tanky=True):
    monster = roll_monster(mid, store.monsters["cave_rat"], store.stat_scaling)
    monster.tile = tile
    monster.floor_number = 5
    if tanky:
        monster.hp = monster.max_hp = 10_000_000
    floor.monsters[mid] = monster
    return monster


def setup(store, floor):
    engine = TickEngine()
    threat_calls = []
    abilities.register(
        engine, floor,
        abilities_cfg=store.abilities, monsters_cfg=store.monsters,
        combat_constants=store.combat_constants, xp_rates=store.xp_rates,
        xp_table=store.xp_table, rewards=store.rewards, items=store.items,
        on_threat=lambda m, pid: threat_calls.append((m.id, pid)),
        conversions=store.conversions,
    )
    return engine, threat_calls


def cast(engine, ability_id, q, r, pid="p1"):
    return engine.step([{
        "intent_type": "use_ability", "player_id": pid,
        "ability_id": ability_id, "target_q": q, "target_r": r,
    }]).events


# --- known-abilities derivation --------------------------------------------

def test_core_ability_unknown_below_requirement_known_at_it(store):
    below = make_player(store, arcana=4)      # fireburst needs arcana 5
    assert "fireburst" not in known_abilities(below, store.abilities, store.items)
    at = make_player(store, arcana=5)
    assert "fireburst" in known_abilities(at, store.abilities, store.items)


def test_item_ability_known_only_while_the_granting_item_is_equipped(store):
    player = make_player(store, arcana=99)
    # frost_nova is item-source: even a maxed caster does not know it unaided.
    assert "frost_nova" not in known_abilities(player, store.abilities, store.items)

    granting = _apex_cloak_code(store)
    player.equipment = Equipment(back=granting)
    player.refresh_stats(store.items)
    assert "frost_nova" in known_abilities(player, store.abilities, store.items)

    player.equipment = Equipment()            # take it off
    player.refresh_stats(store.items)
    assert "frost_nova" not in known_abilities(player, store.abilities, store.items)


def _apex_cloak_code(store):
    """The back base that grants frost_nova (CK9 in config)."""
    for code, base in store.item_bases.items():
        if base.get("grants_ability") == "frost_nova":
            return code
    raise AssertionError("no item base grants frost_nova")


# --- single-target parity ---------------------------------------------------

def test_single_target_ability_hits_only_the_tile_it_lands_on(store):
    floor = make_floor()
    player = make_player(store, strength=200)   # knows heavy_strike (str 5)
    floor.players["p1"] = player
    on = add_monster(store, floor, (0, 1), "on")
    off = add_monster(store, floor, (0, 2), "off")   # aoe_radius 0: untouched
    engine, _ = setup(store, floor)

    events = cast(engine, "heavy_strike", 0, 1)
    hits = {e["target"] for e in events if e.get("type") == "combat_result"}
    assert hits == {"on"}
    assert off.hp == off.max_hp


# --- AoE --------------------------------------------------------------------

def test_aoe_hits_every_monster_in_radius(store):
    import random
    floor = make_floor()
    player = make_player(store, arcana=200)
    floor.players["p1"] = player
    # Impact (0,3); radius 1 covers the centre and its neighbours.
    centre = add_monster(store, floor, (0, 3), "centre")
    near = add_monster(store, floor, (1, 3), "near")     # distance 1
    far = add_monster(store, floor, (0, 5), "far")       # distance 2, out
    engine, threat = setup(store, floor)

    # Seed locally (and restore) so the two in-radius hits do not ride on the
    # global RNG state left by whatever tests ran before -- even at precision
    # 400 a bare ~5% miss chance per target makes an unseeded hit-set assertion
    # flaky. State is restored so downstream tests see the RNG they otherwise
    # would.
    rng_state = random.getstate()
    random.seed(1)
    try:
        events = cast(engine, "fireburst", 0, 3)
    finally:
        random.setstate(rng_state)
    struck = {e["target"] for e in events
              if e.get("type") == "combat_result" and e["result"] == "hit"}
    assert {"centre", "near"} <= struck
    assert "far" not in struck
    assert far.hp == far.max_hp
    # Every monster hit is now aggroed on the caster.
    assert ("centre", "p1") in threat and ("near", "p1") in threat


def test_player_aoe_never_hits_other_players(store):
    floor = make_floor()
    caster = make_player(store, arcana=200)
    floor.players["p1"] = caster
    bystander = Player(
        id="p2", name="Ally", tower_id="tower-a", floor_number=5, tile=(0, 3),
        hp=500, max_hp=500, mana=0, max_mana=0, weapon_id="unarmed", skills=Skills(),
    )
    floor.players["p2"] = bystander
    add_monster(store, floor, (0, 3), "m1")
    engine, _ = setup(store, floor)

    cast(engine, "fireburst", 0, 3)
    assert bystander.hp == bystander.max_hp, "friendly fire hit another player"


# --- gates ------------------------------------------------------------------

def test_cooldown_blocks_a_second_cast(store):
    floor = make_floor()
    floor.players["p1"] = make_player(store, strength=200)
    add_monster(store, floor, (0, 1), "m1")
    engine, _ = setup(store, floor)

    first = cast(engine, "heavy_strike", 0, 1)
    assert any(e.get("type") == "ability_used" for e in first)
    second = cast(engine, "heavy_strike", 0, 1)
    assert any(e.get("reason") == "on cooldown" for e in second)


def test_mana_gate_blocks_when_short(store):
    floor = make_floor()
    player = make_player(store, arcana=200)
    player.mana = 0                              # fireburst costs 12
    floor.players["p1"] = player
    add_monster(store, floor, (0, 3), "m1")
    engine, _ = setup(store, floor)

    events = cast(engine, "fireburst", 0, 3)
    assert any(e.get("reason") == "not enough mana" for e in events)


def test_range_gate_blocks_a_far_target(store):
    floor = make_floor()
    floor.players["p1"] = make_player(store, arcana=200, tile=(0, 0))
    add_monster(store, floor, (0, 7), "m1")      # fireburst range 6
    engine, _ = setup(store, floor)

    events = cast(engine, "fireburst", 0, 7)
    assert any(e.get("reason") == "out of range" for e in events)


def test_unknown_or_locked_ability_is_rejected(store):
    floor = make_floor()
    floor.players["p1"] = make_player(store, arcana=1)   # below fireburst req
    add_monster(store, floor, (0, 3), "m1")
    engine, _ = setup(store, floor)

    events = cast(engine, "fireburst", 0, 3)
    assert any(e.get("reason") == "unknown ability" for e in events)


def test_mana_is_spent_on_a_successful_cast(store):
    floor = make_floor()
    player = make_player(store, arcana=200)
    player.mana = 100
    floor.players["p1"] = player
    add_monster(store, floor, (0, 3), "m1")
    engine, _ = setup(store, floor)

    cast(engine, "fireburst", 0, 3)
    assert player.mana == 100 - store.abilities["fireburst"]["cost"]["mana"]


# --- monster casts ----------------------------------------------------------

def _schedule_monster_ability(engine, mid, ability_id, q, r):
    from gep.actions import MONSTER_ABILITY
    engine.schedule(0, MONSTER_ABILITY, {
        "monster_id": mid, "ability_id": ability_id, "target_q": q, "target_r": r,
    })
    return engine.step([]).events


def test_monster_ability_resolves_against_players_only(store):
    floor = make_floor()
    victim = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 1),
        hp=500, max_hp=500, mana=0, max_mana=0, weapon_id="unarmed", skills=Skills(),
    )
    floor.players["p1"] = victim
    caster = add_monster(store, floor, (0, 0), "caster")
    bystander = add_monster(store, floor, (0, 1), "bystander")  # on the impact tile
    engine, _ = setup(store, floor)

    events = _schedule_monster_ability(engine, "caster", "heavy_strike", 0, 1)
    targets = {e["target"] for e in events if e.get("type") == "combat_result"}
    assert targets == {"p1"}, "monster ability must hit players, not other monsters"
    assert bystander.hp == bystander.max_hp


def test_monster_ability_is_cooldown_gated(store):
    floor = make_floor()
    floor.players["p1"] = Player(
        id="p1", name="Hero", tower_id="tower-a", floor_number=5, tile=(0, 1),
        hp=5000, max_hp=5000, mana=0, max_mana=0, weapon_id="unarmed", skills=Skills(),
    )
    add_monster(store, floor, (0, 0), "caster")
    engine, _ = setup(store, floor)

    first = _schedule_monster_ability(engine, "caster", "heavy_strike", 0, 1)
    assert any(e.get("type") == "ability_used" for e in first)
    # Immediately again: still cooling down, so nothing resolves.
    second = _schedule_monster_ability(engine, "caster", "heavy_strike", 0, 1)
    assert not any(e.get("type") == "ability_used" for e in second)
