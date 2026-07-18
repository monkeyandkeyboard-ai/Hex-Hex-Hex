"""Floor exit system. Registers the use-exit intent: player steps onto an
up/down exit tile and requests to move to the adjacent floor.
"""
from gep.floor_state import FloorState
from gep.tick import TickEngine


def register(engine: TickEngine, floor: FloorState, on_change_floor) -> None:
    """on_change_floor(player_id, direction) is a callback the server
    entrypoint provides to actually move the player between floor states.
    """
    def handle_use_exit(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        direction = intent.get("direction")  # "up" or "down"
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return [{"type": "error", "reason": "invalid player", "player_id": player_id}]

        layout = floor.layout
        if direction == "up":
            if player.tile != layout.up_exit:
                return [{"type": "error", "reason": "not on up exit", "player_id": player_id}]
            on_change_floor(player_id, "up")
            return [{"type": "floor_change", "player_id": player_id, "direction": "up",
                     "new_floor": layout.floor_number + 1}]
        elif direction == "down":
            if layout.down_exit is None:
                return [{"type": "error", "reason": "no down exit on this floor", "player_id": player_id}]
            if player.tile != layout.down_exit:
                return [{"type": "error", "reason": "not on down exit", "player_id": player_id}]
            on_change_floor(player_id, "down")
            return [{"type": "floor_change", "player_id": player_id, "direction": "down",
                     "new_floor": layout.floor_number - 1}]
        else:
            return [{"type": "error", "reason": "direction must be 'up' or 'down'", "player_id": player_id}]

    engine.register_intent_handler("use-exit", handle_use_exit)
