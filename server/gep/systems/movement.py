"""Movement system. Registers intent and action handlers onto the TickEngine.

Move model:
- Player sends intent {intent_type:"move-to-tile", player_id, target_q, target_r}
- Server computes A* path, stores it in player state, schedules first MOVE_STEP
- Each MOVE_STEP handler advances the player by `speed` tiles along the path,
  emitting a position event per tile moved (backbone for client animation -- the
  client receives the full sequence of intermediate positions, not just endpoints)
- If path isn't exhausted, another MOVE_STEP is scheduled for the next tick

Movement speed is a property of the player (or their active transport).
Base speed = 1 tile/tick. Transport can grant speed 2+.
"""
from gep.floor_state import FloorState
from gep.pathfinding import find_path
from gep.tick import TickEngine

Tile = tuple[int, int]


def _tile_from_intent(intent: dict, q_key: str = "target_q", r_key: str = "target_r") -> Tile:
    return (int(intent[q_key]), int(intent[r_key]))


def register(engine: TickEngine, floor: FloorState) -> None:
    def handle_move_intent(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        player = floor.players.get(player_id)
        if player is None:
            return [{"type": "error", "reason": f"unknown player {player_id!r}"}]
        if not player.alive:
            return [{"type": "error", "reason": "dead players cannot move", "player_id": player_id}]

        target = _tile_from_intent(intent)
        if not floor.is_valid_tile(target):
            return [{"type": "error", "reason": "target tile not on this floor", "player_id": player_id}]

        if player.tile == target:
            return []

        path = find_path(player.tile, target, floor.is_passable)
        if path is None:
            return [{"type": "error", "reason": "no path to target", "player_id": player_id}]

        # path[0] is current tile; skip it, the rest is what remains to walk
        remaining = path[1:]
        if not remaining:
            return []

        eng.schedule(1, "move-step", {
            "player_id": player_id,
            "remaining": remaining,
            "speed": getattr(player, "move_speed", 1),
        })
        return [{"type": "move_started", "player_id": player_id, "path": path}]

    def handle_move_step(payload: dict, eng: TickEngine) -> list[dict]:
        player_id = payload["player_id"]
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return []

        remaining: list[Tile] = [tuple(t) for t in payload["remaining"]]
        speed: int = payload["speed"]

        events: list[dict] = []
        steps_taken = 0
        while remaining and steps_taken < speed:
            next_tile = remaining[0]
            is_last = len(remaining) == 1
            if not is_last and not floor.is_passable(next_tile):
                # Intermediate tile became impassable since path was computed
                # (e.g. a monster stepped into it). Stop at current position;
                # client must re-issue a move intent to route around.
                events.append({"type": "move_blocked", "player_id": player_id, "tile": list(player.tile)})
                return events
            remaining.pop(0)
            player.tile = next_tile
            events.append({"type": "position_update", "player_id": player_id, "tile": list(next_tile)})
            steps_taken += 1

        if remaining:
            eng.schedule(1, "move-step", {
                "player_id": player_id,
                "remaining": remaining,
                "speed": speed,
            })

        return events

    engine.register_intent_handler("move-to-tile", handle_move_intent)
    engine.register_action_handler("move-step", handle_move_step)
