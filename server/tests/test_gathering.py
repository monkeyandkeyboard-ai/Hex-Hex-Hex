import pathlib

import pytest

from gep.config_loader import ConfigStore
from gep.entities import Player, Skills
from gep.floorgen import FloorLayout
from gep.floor_state import FloorState
from gep.systems import gathering
from gep.tick import TickEngine

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def store():
    return ConfigStore(CONFIG_DIR)


NODE_TILE = (2, 0)
RESOURCE_ID = "iron_ore"


def make_floor_with_node(node_tile=NODE_TILE, resource_id=RESOURCE_ID):
    layout = FloorLayout(
        tower_id="tower-a", floor_number=5, radius=8,
        tiles=[(q, r) for q in range(-8, 9) for r in range(-8, 9)
               if abs(q) <= 8 and abs(r) <= 8 and abs(q + r) <= 8],
        up_exit=(8, 0), down_exit=(-8, 0),
    )
    floor = FloorState.from_layout(layout, resource_nodes={node_tile: resource_id})
    return floor


def make_player(pid="p1", tile=NODE_TILE):
    return Player(
        id=pid, name="Hero", tower_id="tower-a", floor_number=5, tile=tile,
        hp=1000, max_hp=1000, mana=50, max_mana=50, weapon_id="unarmed",
        skills=Skills(),
    )


def setup_engine(floor, store):
    engine = TickEngine()
    gathering.register(engine, floor, store.resources, store.xp_table)
    return engine


def test_gather_awards_items_and_xp_after_gather_ticks(store):
    floor = make_floor_with_node()
    player = make_player()
    floor.players["p1"] = player
    engine = setup_engine(floor, store)

    gather_ticks = store.resources[RESOURCE_ID]["gather_ticks"]

    engine.step([{"intent_type": "gather-node", "player_id": "p1",
                  "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])

    # Should not fire yet
    for _ in range(gather_ticks - 1):
        result = engine.step([])
        assert not any(e["type"] == "item_gained" for e in result.events)

    # Should fire now
    result = engine.step([])
    item_events = [e for e in result.events if e["type"] == "item_gained"]
    assert len(item_events) == 1
    assert item_events[0]["item_id"] == RESOURCE_ID
    assert item_events[0]["quantity"] >= store.resources[RESOURCE_ID]["yield"]["min"]


def test_gather_depletes_node(store):
    floor = make_floor_with_node()
    player = make_player()
    floor.players["p1"] = player
    engine = setup_engine(floor, store)

    gather_ticks = store.resources[RESOURCE_ID]["gather_ticks"]
    engine.step([{"intent_type": "gather-node", "player_id": "p1",
                  "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])
    for _ in range(gather_ticks):
        engine.step([])

    assert NODE_TILE not in floor.resource_nodes
    assert NODE_TILE in floor.depleted_nodes


def test_node_respawns_after_respawn_ticks(store):
    floor = make_floor_with_node()
    player = make_player()
    floor.players["p1"] = player
    engine = setup_engine(floor, store)

    resource = store.resources[RESOURCE_ID]
    total_ticks = resource["gather_ticks"] + resource["respawn_ticks"]

    engine.step([{"intent_type": "gather-node", "player_id": "p1",
                  "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])
    for _ in range(total_ticks):
        engine.step([])

    assert NODE_TILE in floor.resource_nodes
    assert NODE_TILE not in floor.depleted_nodes


def test_gather_depleted_node_returns_error(store):
    floor = make_floor_with_node()
    player = make_player()
    floor.players["p1"] = player
    floor.depleted_nodes[NODE_TILE] = 99999  # manually mark depleted
    floor.resource_nodes.pop(NODE_TILE)
    engine = setup_engine(floor, store)

    result = engine.step([{"intent_type": "gather-node", "player_id": "p1",
                           "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])
    assert any(e["type"] == "error" for e in result.events)


def test_gather_wrong_tile_returns_error(store):
    floor = make_floor_with_node()
    player = make_player(tile=(0, 0))  # not on the node tile
    floor.players["p1"] = player
    engine = setup_engine(floor, store)

    result = engine.step([{"intent_type": "gather-node", "player_id": "p1",
                           "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])
    assert any(e["type"] == "error" for e in result.events)


def test_gather_awards_xp_to_correct_skills(store):
    floor = make_floor_with_node()
    player = make_player()
    floor.players["p1"] = player
    engine = setup_engine(floor, store)

    resource = store.resources[RESOURCE_ID]
    gather_ticks = resource["gather_ticks"]

    engine.step([{"intent_type": "gather-node", "player_id": "p1",
                  "tile_q": NODE_TILE[0], "tile_r": NODE_TILE[1]}])
    for _ in range(gather_ticks):
        engine.step([])

    for skill, amount in resource["xp"].items():
        if skill in player.skills.combat_xp:
            assert player.skills.combat_xp[skill] == amount
        else:
            assert player.skills.non_combat_xp.get(skill, 0) == amount
