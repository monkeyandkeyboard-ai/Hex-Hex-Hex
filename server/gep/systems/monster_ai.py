"""Monster behaviour: wandering, threat acquisition, and pursuit.

This module owns every decision about *where a monster goes* and nothing
about damage. Combat never imports it and it never imports combat. The only
channel between them is the `notify_threat` callback returned by register():
combat calls it to report "this entity was attacked by that player", and
what the monster does about it is decided entirely here.

That seam is the point. Retuning damage scaling touches combat.py and its
constants; retuning pursuit touches this file and the `movement` config
block. Neither can break the other, because neither can see the other.

Behaviour model, evaluated on one recurring per-monster action:

    threat slot empty  -> wander to a random free neighbour
    threat slot filled -> path toward the target and take one step,
                          stopping when adjacent (never onto the player)

Cadence for both modes is per-template config, not constants here.
"""
import random

from gep.actions import CLEAR_THREAT, MONSTER_STRIKE
from gep.floor_state import FloorState
from gep.hexgrid import facing_from_delta, hex_distance
from gep.pathfinding import find_path, hex_neighbors
from gep.tick import TickEngine

THINK_ACTION = "monster-think"


def register(
    engine: TickEngine,
    floor: FloorState,
    monsters_cfg: dict,
    rng: random.Random | None = None,
):
    """Returns `notify_threat(entity, attacker_id)` for combat to call."""
    roll = rng or random

    # Reverse index: player id -> ids of monsters holding that player in their
    # threat table. This is what makes dropping a dead player O(1) in the
    # number of monsters on the floor; a per-monster dict alone would not,
    # since finding *which* monsters to edit would still mean sweeping them
    # all. Every write to a threat table goes through the helpers below so the
    # two structures cannot drift apart.
    threatened_by: dict[str, set[str]] = {}

    def add_threat(monster, player_id: str, amount: float = 1.0) -> None:
        monster.threat_table[player_id] = monster.threat_table.get(player_id, 0.0) + amount
        threatened_by.setdefault(player_id, set()).add(monster.id)

    def drop_threat(monster, player_id: str) -> None:
        monster.threat_table.pop(player_id, None)
        holders = threatened_by.get(player_id)
        if holders is not None:
            holders.discard(monster.id)
            if not holders:
                del threatened_by[player_id]

    def movement_cfg(template_id: str) -> dict:
        return monsters_cfg.get(template_id, {}).get("movement", {})

    def interval_for(monster) -> int:
        cfg = movement_cfg(monster.template_id)
        key = "pursue_interval_ticks" if monster.threat_target else "wander_interval_ticks"
        default = 2 if monster.threat_target else 6
        return max(1, int(cfg.get(key, default)))

    def face_toward(monster, tile) -> list[dict]:
        """Turn to look at a tile without moving (used when already adjacent).

        Emits on change: a turn-in-place is still a state change the client
        has to hear about, or the server and client disagree about which
        sprite frame to draw until the monster happens to move again.
        """
        facing = facing_from_delta(monster.tile, tile)
        if facing is None or facing == monster.facing:
            return []
        monster.facing = facing
        return [{
            "type": "monster_moved",
            "monster_id": monster.id,
            "tile": list(monster.tile),   # unchanged -- the client won't glide
            "facing": monster.facing,
        }]

    def step_to(monster, target_tile) -> list[dict]:
        facing = facing_from_delta(monster.tile, target_tile)
        monster.tile = target_tile
        if facing is not None:
            monster.facing = facing
        return [{
            "type": "monster_moved",
            "monster_id": monster.id,
            "tile": list(target_tile),
            "facing": monster.facing,
        }]

    def wander(monster) -> list[dict]:
        cfg = movement_cfg(monster.template_id)
        if roll.random() >= float(cfg.get("wander_chance", 0.5)):
            return []  # idle this cycle, so monsters don't march in lockstep

        # Free neighbours only: is_passable already rejects tiles off-floor or
        # held by another monster; players block too (walking through someone
        # is worse than standing still).
        options = [
            t for t in hex_neighbors(*monster.tile)
            if floor.is_passable(t) and floor.player_at(t) is None
        ]
        if not options:
            return []
        return step_to(monster, roll.choice(options))

    def attack_range(monster) -> int:
        combat = monsters_cfg.get(monster.template_id, {}).get("combat", {})
        return int(combat.get("attack_range_tiles", 1))

    def pursue(monster, eng: TickEngine) -> list[dict]:
        """One step along the path to the hunted player.

        The target's live tile is fed to pathfinding every cycle rather than a
        path being cached, so the monster re-routes as the player moves.
        """
        target_id = monster.threat_target
        target = floor.players.get(target_id)
        if target is None or not target.alive:
            # Target left the floor or died -- drop just that entry and resume
            # wandering (or fall through to the next-highest threat on the
            # following cycle) rather than freezing in place.
            drop_threat(monster, target_id)
            return []

        # In range: ask for a strike. This module decides *when* an attack is
        # wanted and nothing about what it does -- combat owns the "monster-
        # strike" handler, the cooldown, and the outcome. Scheduling a named
        # action rather than calling combat keeps the two unable to see each
        # other (see the module docstring).
        if hex_distance(monster.tile, target.tile) <= attack_range(monster):
            eng.schedule(0, MONSTER_STRIKE, {
                "monster_id": monster.id,
                "player_id": target.id,
            })

        # find_path treats the goal as reachable even when occupied, so a path
        # to the player always exists if any route does.
        path = find_path(monster.tile, target.tile, floor.is_passable)
        if path is None:
            return []  # walled off for now; keep the threat and retry next cycle

        # path[0] is the current tile, path[-1] is the player's tile. Anything
        # shorter than 3 means we're already adjacent: hold position and turn
        # to face them instead of stepping onto them.
        if len(path) < 3:
            return face_toward(monster, target.tile)

        next_tile = path[1]
        if not floor.is_passable(next_tile) or floor.player_at(next_tile) is not None:
            return []  # another entity took the tile since the path was built
        return step_to(monster, next_tile)

    def handle_think(payload: dict, eng: TickEngine) -> list[dict]:
        monster_id = payload["monster_id"]
        monster = floor.monsters.get(monster_id)
        if monster is None:
            return []  # despawned for good; let the timer die with it

        # Always re-arm, even while dead: the monster respawns under the same
        # id, and rescheduling here keeps combat_system from having to know
        # anything about this timer.
        eng.schedule(interval_for(monster), THINK_ACTION, {"monster_id": monster_id})

        if not monster.alive:
            return []

        return pursue(monster, eng) if monster.threat_target else wander(monster)

    def notify_threat(entity, attacker_id: str) -> None:
        """Combat's one line into this system: damage landed on `entity`.

        Deliberately tolerant -- it takes any entity and ignores anything
        without a threat slot, so combat needs no knowledge of which entity
        kinds have behaviour attached.
        """
        if attacker_id is None:
            return
        if getattr(entity, "threat_table", None) is None:
            return
        add_threat(entity, attacker_id)

    def handle_clear_threat(payload: dict, eng: TickEngine) -> list[dict]:
        """Forget a player entirely -- they died, or otherwise stopped being a
        valid target. Asked for by the respawn system, which knows nothing
        about how threat is stored; this module knows nothing about why.

        Costs one dict lookup plus one deletion per monster that actually held
        the player, rather than a pass over every monster on the floor.
        """
        player_id = payload.get("player_id")
        for monster_id in list(threatened_by.get(player_id, ())):
            monster = floor.monsters.get(monster_id)
            if monster is not None:
                monster.threat_table.pop(player_id, None)
        threatened_by.pop(player_id, None)
        return []

    engine.register_action_handler(THINK_ACTION, handle_think)
    engine.register_action_handler(CLEAR_THREAT, handle_clear_threat)

    # Stagger the first tick per monster so a floor's worth of them doesn't
    # step in unison on the same tick.
    for monster_id, monster in floor.monsters.items():
        engine.schedule(roll.randint(1, interval_for(monster)), THINK_ACTION,
                        {"monster_id": monster_id})

    return notify_threat
