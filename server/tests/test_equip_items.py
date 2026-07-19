"""Equipping rolled item instances: slot resolution, the two-hand rule, and
the swap-at-a-full-pack case."""
import pathlib
import random

import pytest

from gep.config_loader import ConfigStore
from gep.entities import INVENTORY_SIZE, Player, Skills
from gep.floor_state import FloorState
from gep.floorgen import generate_floor
from gep.items import encode_instance
from gep.systems import inventory_system
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"

RULESET = {
    "radius": 6,
    "resource_spawn_chance": 0.0,
    "resource_weights": [["iron_ore", 1]],
    "monster_spawn_count": 0,
    "monster_weights": [["cave_rat", 1]],
}


@pytest.fixture(scope="module")
def store():
    return ConfigStore(CONFIG_DIR)


def setup(store):
    floor = FloorState.from_layout(generate_floor("tower-a", 5, "test", RULESET))
    player = Player(
        id="p1", name="Tester", tower_id="tower-a", floor_number=5, tile=(0, 0),
        hp=100, max_hp=100, mana=0, max_mana=0,
        weapon_id=store.default_equipment_state, skills=Skills(),
    )
    floor.players["p1"] = player
    engine = TickEngine()
    inventory_system.register(engine, floor, store.weapons,
                              store.default_equipment_state, store.items)
    return floor, player, engine


def equip(engine, slot):
    return engine.step([{"intent_type": "equip-item", "player_id": "p1", "inv_slot": slot}]).events


def unequip(engine, slot):
    return engine.step([{"intent_type": "unequip-item", "player_id": "p1", "equip_slot": slot}]).events


def give(player, item_id):
    player.add_item(item_id, 1)
    return next(i for i in range(INVENTORY_SIZE)
                if player.inventory.get(i) and player.inventory[i]["item_id"] == item_id)


# --- slot resolution -------------------------------------------------------

@pytest.mark.parametrize("base_code,expected", [
    ("S1", "main_hand"),
    ("C1", "off_hand"),
    ("PH1", "head"),
    ("LT1", "torso"),
    ("CG1", "hands"),
    ("PL1", "legs"),
    ("LF1", "feet"),
    ("CK1", "back"),
])
def test_instances_equip_to_their_declared_slot(store, base_code, expected):
    floor, player, engine = setup(store)
    instance = store.items.roll_instance(base_code, random.Random(1))
    equip(engine, give(player, instance))
    assert getattr(player.equipment, expected) == instance


def test_two_handed_item_occupies_main_hand(store):
    floor, player, engine = setup(store)
    greatsword = store.items.roll_instance("G1", random.Random(2))
    equip(engine, give(player, greatsword))
    assert player.equipment.main_hand == greatsword
    assert player.weapon_id == greatsword


def test_material_is_not_equippable(store):
    floor, player, engine = setup(store)
    events = equip(engine, give(player, "copper_ore"))
    assert events[0]["type"] == "error"
    assert "not equippable" in events[0]["reason"]


# --- the two-hand rule -----------------------------------------------------

def test_equipping_two_hander_clears_the_off_hand(store):
    floor, player, engine = setup(store)
    shield = store.items.roll_instance("C1", random.Random(3))
    equip(engine, give(player, shield))
    assert player.equipment.off_hand == shield

    greatsword = store.items.roll_instance("G1", random.Random(4))
    equip(engine, give(player, greatsword))

    assert player.equipment.main_hand == greatsword
    assert player.equipment.off_hand is None, "a two-hander must empty the off hand"
    assert shield in [s["item_id"] for s in player.inventory_snapshot() if s]


def test_equipping_off_hand_puts_away_a_two_hander(store):
    floor, player, engine = setup(store)
    greatsword = store.items.roll_instance("G1", random.Random(5))
    equip(engine, give(player, greatsword))

    shield = store.items.roll_instance("C1", random.Random(6))
    equip(engine, give(player, shield))

    assert player.equipment.off_hand == shield
    assert player.equipment.main_hand is None
    assert player.weapon_id == store.default_equipment_state
    assert greatsword in [s["item_id"] for s in player.inventory_snapshot() if s]


def test_one_hander_and_shield_coexist(store):
    floor, player, engine = setup(store)
    sword = store.items.roll_instance("S1", random.Random(7))
    shield = store.items.roll_instance("C1", random.Random(8))
    equip(engine, give(player, sword))
    equip(engine, give(player, shield))
    assert player.equipment.main_hand == sword
    assert player.equipment.off_hand == shield


# --- inventory pressure ----------------------------------------------------

def test_straight_swap_succeeds_at_a_full_pack(store):
    """The equipping item vacates its own slot, so a one-for-one swap must
    fit even with 28/28 used."""
    floor, player, engine = setup(store)
    worn = store.items.roll_instance("S1", random.Random(9))
    equip(engine, give(player, worn))

    incoming = store.items.roll_instance("S2", random.Random(10))
    slot = give(player, incoming)
    for i in range(INVENTORY_SIZE):
        if player.inventory.get(i) is None:
            player.inventory[i] = {"item_id": "copper_ore", "quantity": 1}

    events = equip(engine, slot)
    assert events[0]["type"] == "equipment_update"
    assert player.equipment.main_hand == incoming
    assert worn in [s["item_id"] for s in player.inventory_snapshot() if s]


def test_two_for_one_swap_is_refused_when_it_cannot_fit(store):
    """A two-hander displacing both hands needs two free slots. Refusing must
    leave the player exactly as they were, not holding neither item."""
    floor, player, engine = setup(store)
    sword = store.items.roll_instance("S1", random.Random(11))
    shield = store.items.roll_instance("C1", random.Random(12))
    equip(engine, give(player, sword))
    equip(engine, give(player, shield))

    greatsword = store.items.roll_instance("G1", random.Random(13))
    slot = give(player, greatsword)
    for i in range(INVENTORY_SIZE):
        if player.inventory.get(i) is None:
            player.inventory[i] = {"item_id": "copper_ore", "quantity": 1}

    events = equip(engine, slot)
    assert events[0]["type"] == "error"
    assert events[0]["reason"] == "inventory full"
    assert player.equipment.main_hand == sword
    assert player.equipment.off_hand == shield
    assert player.inventory[slot]["item_id"] == greatsword


# --- instances never stack -------------------------------------------------

def test_two_rolls_of_one_base_occupy_separate_slots(store):
    floor, player, engine = setup(store)
    a = store.items.roll_instance("S1", random.Random(14))
    b = store.items.roll_instance("S1", random.Random(15))
    player.add_item(a, 1)
    player.add_item(b, 1)
    held = [s for s in player.inventory_snapshot() if s]
    assert len(held) == 2, "distinct rolls must not merge into one stack"
    assert all(s["quantity"] == 1 for s in held)


def test_identical_instance_strings_still_do_not_stack(store):
    """Two rolls can collide on the same string; they are still two items."""
    floor, player, engine = setup(store)
    instance = encode_instance("S1", [])
    player.add_item(instance, 1)
    player.add_item(instance, 1)
    held = [s for s in player.inventory_snapshot() if s]
    assert len(held) == 2
