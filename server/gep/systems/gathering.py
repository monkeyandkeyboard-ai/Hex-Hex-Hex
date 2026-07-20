"""Gathering system. Registers intent and action handlers onto the TickEngine.

Gather model:
- Player sends intent {intent_type:"gather-node", player_id, tile_q, tile_r}
- Server validates: tile has a resource node, player is on the tile (or
  adjacent -- [OPEN], using on-tile for simplicity until defined), node is
  not depleted
- Schedules gather-complete at now + resource.gather_ticks
- gather-complete: rolls yield count, awards XP, marks node depleted,
  schedules respawn-node at now + resource.respawn_ticks
- respawn-node: restores the node in floor.resource_nodes

All values (gather_ticks, respawn_ticks, yield range, XP block) come from
the resource config -- no hardcoded numbers here.
"""
import random

from gep import crossdomain
from gep.floor_state import FloorState
from gep.tick import TickEngine
from gep.xp import award_xp_block

Tile = tuple[int, int]


def register(engine: TickEngine, floor: FloorState, resources: dict, xp_table: dict,
             conversions: list | None = None) -> None:
    conversions = conversions or []

    def utility(player, target: str, base: float) -> float:
        """This player's current value for a utility stat.

        Built fresh rather than read off a cache -- see gep/crossdomain.py
        for why. The floor number comes from the floor this system was
        registered against, which is the one the player is standing on.
        """
        return crossdomain.resolve(
            player, floor.layout.floor_number, conversions, target, base
        )

    def handle_gather_intent(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return [{"type": "error", "reason": "invalid player", "player_id": player_id}]

        tile: Tile = (int(intent["tile_q"]), int(intent["tile_r"]))
        resource_id = floor.resource_nodes.get(tile)
        if resource_id is None:
            return [{"type": "error", "reason": "no resource node at tile", "player_id": player_id}]
        if tile in floor.depleted_nodes:
            return [{"type": "error", "reason": "resource node is depleted", "player_id": player_id}]

        resource = resources.get(resource_id)
        if resource is None:
            return [{"type": "error", "reason": f"unknown resource {resource_id!r}"}]

        # [OPEN] adjacency vs same-tile requirement. Using same-tile for now.
        if player.tile != tile:
            return [{"type": "error", "reason": "player is not on the resource tile", "player_id": player_id}]

        # Gather speed shortens the swing rather than adding to it, so it
        # divides the configured duration. Floored at one tick: a fast enough
        # gatherer must still take a turn, or the action stops existing.
        speed = utility(player, "gather_speed", 1.0)
        gather_ticks = max(1, round(resource["gather_ticks"] / speed)) if speed > 0 else resource["gather_ticks"]
        eng.schedule(gather_ticks, "gather-complete", {
            "player_id": player_id,
            "tile": list(tile),
            "resource_id": resource_id,
        })
        return [{"type": "gather_started", "player_id": player_id, "tile": list(tile), "resource_id": resource_id}]

    def handle_gather_complete(payload: dict, eng: TickEngine) -> list[dict]:
        player_id = payload["player_id"]
        player = floor.players.get(player_id)
        tile: Tile = tuple(payload["tile"])
        resource_id = payload["resource_id"]
        resource = resources.get(resource_id)

        events: list[dict] = []

        # Player or node may have become invalid between scheduling and now
        if player is None or not player.alive:
            return events
        if floor.resource_nodes.get(tile) != resource_id or tile in floor.depleted_nodes:
            return events
        if resource is None:
            return events

        # Roll yield count, then scale it by the player's gathering power.
        # The roll happens first so proficiency widens the whole range rather
        # than raising the floor -- a lucky novice and an unlucky expert
        # should still be able to trade places.
        yield_cfg = resource["yield"]
        yield_count = random.randint(yield_cfg["min"], yield_cfg["max"])
        yield_count = max(1, round(utility(player, "gather_yield", yield_count)))

        # Add item to player inventory; emit item_gained regardless (for log)
        player.add_item(resource_id, yield_count)
        events.append({
            "type": "item_gained",
            "player_id": player_id,
            "item_id": resource_id,
            "quantity": yield_count,
            "inventory": player.inventory_snapshot(),
        })

        # Award XP for every skill in the resource's XP block
        xp_events = award_xp_block(player, resource["xp"], xp_table)
        events.extend(xp_events)

        # Mark node depleted
        respawn_ticks = resource["respawn_ticks"]
        floor.resource_nodes.pop(tile, None)
        floor.depleted_nodes[tile] = eng.tick + respawn_ticks

        events.append({"type": "node_depleted", "tile": list(tile), "resource_id": resource_id})

        eng.schedule(respawn_ticks, "respawn-node", {"tile": list(tile), "resource_id": resource_id})
        return events

    def handle_respawn_node(payload: dict, eng: TickEngine) -> list[dict]:
        tile: Tile = tuple(payload["tile"])
        resource_id = payload["resource_id"]
        floor.depleted_nodes.pop(tile, None)
        floor.resource_nodes[tile] = resource_id
        return [{"type": "node_respawned", "tile": list(tile), "resource_id": resource_id}]

    engine.register_intent_handler("gather-node", handle_gather_intent)
    engine.register_action_handler("gather-complete", handle_gather_complete)
    engine.register_action_handler("respawn-node", handle_respawn_node)
