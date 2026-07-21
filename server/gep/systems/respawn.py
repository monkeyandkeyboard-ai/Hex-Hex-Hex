"""The player defeat lifecycle.

Owns what happens to a player whose health reaches zero, and nothing else.
Combat reports the death by scheduling PLAYER_DEFEATED and stops caring;
behaviour is told to forget the player by way of CLEAR_THREAT. Neither system
imports this one, and this one decides the policy alone -- so respawn rules
can change without touching damage maths or pathfinding.

Sequence, in order:
    1. halt actions and drop aggro
    2. relocate to the player's persistent anchor
    3. restore pools to maximum
    4. flush to disk synchronously, so the relocation survives a crash

Compendium §14 leaves death *severity* [OPEN] -- nothing is dropped or lost on
death, and item loss is deliberately not invented here. The anchor-setting
mechanic is now decided: a player's anchor is the last town (safe floor) they
visited, recorded on arrival by FloorManager and persisted, falling back to
floor 1 for a character who has never entered one.
"""
from gep.actions import CLEAR_THREAT, PLAYER_DEFEATED
from gep.floor_state import FloorState
from gep.tick import TickEngine


def register(
    engine: TickEngine,
    floor: FloorState,
    save_player=None,
    on_relocate=None,
) -> None:
    """`save_player(player)` performs the blocking write; `on_relocate` is
    `FloorManager.move_to_floor(player_id, floor_number, tile)`, needed only
    when the anchor is on a different floor than where the player fell.

    Note this is the *absolute* mover, not the stairs mover: an anchor is a
    destination, and dying on floor 7 with an anchor on floor 1 is one
    relocation rather than six descents.
    """

    def handle_player_defeated(payload: dict, eng: TickEngine) -> list[dict]:
        player_id = payload["player_id"]
        player = floor.players.get(player_id)
        if player is None:
            return []

        # 1. Halt actions. Clearing the engagement stops auto-combat, and the
        # move sequence bump strips any queued movement steps -- a corpse
        # should not keep walking its old path to the tile it died on.
        player.combat_target = None
        player.attack_seq += 1
        player.move_seq += 1

        # Drop aggro through the behaviour system rather than reaching into
        # monster state from here.
        eng.schedule(0, CLEAR_THREAT, {"player_id": player_id})

        # 2. Relocate to the anchor.
        anchor_floor = player.spawn_floor
        anchor_tile = tuple(player.spawn_tile)
        moved_floor = anchor_floor != player.floor_number

        player.tile = anchor_tile
        # 3. Restore pools. alive is set back before the write so a crash
        # cannot leave a character persisted as dead.
        player.hp = player.max_hp
        player.mana = player.max_mana
        player.alive = True

        events = [{
            "type": "player_died",
            "player_id": player_id,
            "killer_id": payload.get("killer_id"),
            "respawn_floor": anchor_floor,
            "respawn_tile": list(anchor_tile),
            "hp": player.hp,
            "max_hp": player.max_hp,
        }]

        if moved_floor and on_relocate is not None:
            # The anchor is on another floor; the server owns floor movement.
            # It re-sends a full snapshot, so no position_update is needed.
            on_relocate(player_id, anchor_floor, anchor_tile)
        else:
            events.append({
                "type": "position_update",
                "player_id": player_id,
                "tile": list(anchor_tile),
                "facing": player.facing,
            })

        # 4. Blocking flush, so the relocation and restored vitals survive a
        # sudden crash. Without this a player could die, be moved, and then
        # come back at the old spot with the old health after an unclean exit.
        if save_player is not None:
            save_player(player)

        return events

    engine.register_action_handler(PLAYER_DEFEATED, handle_player_defeated)
