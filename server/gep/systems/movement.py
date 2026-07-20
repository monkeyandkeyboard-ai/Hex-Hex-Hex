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
from gep import crossdomain
from gep.floor_state import FloorState
from gep.hexgrid import facing_from_delta
from gep.pathfinding import find_path
from gep.tick import TickEngine

Tile = tuple[int, int]


def _tile_from_intent(intent: dict, q_key: str = "target_q", r_key: str = "target_r") -> Tile:
    return (int(intent[q_key]), int(intent[r_key]))


def register(engine: TickEngine, floor: FloorState, on_move=None,
             conversions: list | None = None) -> None:
    """`on_move(player) -> events` is called when a move intent is accepted.

    It exists so that issuing a movement command can break auto-combat
    without this module knowing what combat is. Movement decides "the player
    chose to move"; whatever else cares about that decides what it means.
    """
    conversions = conversions or []

    def move_speed(player) -> int:
        """Tiles per tick, after utility modifiers.

        Truncated to a whole number of tiles: the step loop advances tile by
        tile, so a speed of 1.8 is a speed of 1 until it reaches 2. That
        makes the stat lumpy by nature, which is a property of the movement
        model rather than something to smooth over here.
        """
        base = getattr(player, "move_speed", 1)
        resolved = crossdomain.resolve(
            player, floor.layout.floor_number, conversions, "move_speed", base
        )
        return max(1, int(resolved))

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

        # Rejected here rather than left to A*: find_path admits the goal
        # regardless of the predicate (so you can path into a monster to attack
        # it), which means an impassable destination would otherwise return a
        # perfectly good path ending on a cliff face.
        if not floor.is_terrain_passable(target):
            return [{"type": "error", "reason": "target tile is impassable", "player_id": player_id}]

        if player.tile == target:
            return []

        path = find_path(player.tile, target, floor.is_passable)
        if path is None:
            return [{"type": "error", "reason": "no path to target", "player_id": player_id}]

        # path[0] is current tile; skip it, the rest is what remains to walk
        remaining = path[1:]
        if not remaining:
            return []

        # Bump the sequence number so any move-step already queued from a
        # prior intent (e.g. player was mid-walk when they clicked again)
        # sees it doesn't match and drops. This lets a new click override
        # the next tick's movement instead of fighting the old path.
        player.move_seq += 1

        # Schedule at delay=0: the tick engine drains intents BEFORE actions,
        # so this step fires in the same tick's action drain -- alongside any
        # stale step from the old path, which the seq check drops. Net effect:
        # click and the player advances this tick in the new direction, no
        # dead tick between old and new path.
        eng.schedule(0, "move-step", {
            "player_id": player_id,
            "remaining": remaining,
            "speed": move_speed(player),
            "seq": player.move_seq,
        })

        events = [{"type": "move_started", "player_id": player_id, "path": path}]
        # Deciding to move is deciding to stop whatever you were doing.
        if on_move is not None:
            events.extend(on_move(player) or [])
        return events

    def handle_move_step(payload: dict, eng: TickEngine) -> list[dict]:
        player_id = payload["player_id"]
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return []

        # Stale steps from a superseded move intent -- ignore.
        if payload.get("seq", 0) != player.move_seq:
            return []

        remaining: list[Tile] = [tuple(t) for t in payload["remaining"]]
        speed: int = payload["speed"]

        events: list[dict] = []
        steps_taken = 0
        while remaining and steps_taken < speed:
            next_tile = remaining[0]
            is_last = len(remaining) == 1
            # Terrain is checked on every tile, entities only on intermediate
            # ones. The final tile may legitimately hold a monster -- that is
            # how you close to melee -- but it may never be terrain the biome
            # forbids, and this is the last gate before the coordinate write.
            if not floor.is_terrain_passable(next_tile) or (
                not is_last and not floor.is_passable(next_tile)
            ):
                # Blocked since the path was computed (e.g. a monster stepped
                # into it). Stop at current position; the client must re-issue
                # a move intent to route around.
                events.append({"type": "move_blocked", "player_id": player_id, "tile": list(player.tile)})
                return events
            remaining.pop(0)
            facing = facing_from_delta(player.tile, next_tile)
            if facing is not None:
                player.facing = facing
            player.tile = next_tile
            events.append({
                "type": "position_update",
                "player_id": player_id,
                "tile": list(next_tile),
                "facing": player.facing,
            })
            steps_taken += 1

        if remaining:
            eng.schedule(1, "move-step", {
                "player_id": player_id,
                "remaining": remaining,
                "speed": speed,
                "seq": payload["seq"],
            })

        return events

    engine.register_intent_handler("move-to-tile", handle_move_intent)
    engine.register_action_handler("move-step", handle_move_step)
