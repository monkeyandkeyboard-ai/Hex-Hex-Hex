"""Monster wander AI. Registers a per-monster recurring action on the tick
engine that steps the monster to a random adjacent tile and updates its
facing.

Deliberately dumb for now -- no aggro, no pursuit, no leashing. Its job is to
exercise the facing/movement plumbing end to end (server state -> event ->
client spritesheet frame) so those paths are proven before real AI lands.

Cadence and restlessness are per-template config (`movement` block), not
constants here, so tuning stays in the config layer.
"""
import random

from gep.floor_state import FloorState
from gep.hexgrid import facing_from_delta
from gep.pathfinding import hex_neighbors
from gep.tick import TickEngine

WANDER_ACTION = "monster-wander"


def register(
    engine: TickEngine,
    floor: FloorState,
    monsters_cfg: dict,
    rng: random.Random | None = None,
) -> None:
    roll = rng or random

    def movement_cfg(template_id: str) -> dict:
        return monsters_cfg.get(template_id, {}).get("movement", {})

    def interval_for(template_id: str) -> int:
        return max(1, int(movement_cfg(template_id).get("wander_interval_ticks", 6)))

    def handle_wander(payload: dict, eng: TickEngine) -> list[dict]:
        monster_id = payload["monster_id"]
        monster = floor.monsters.get(monster_id)
        if monster is None:
            return []  # despawned for good; let the timer die with it

        interval = interval_for(monster.template_id)
        # Always re-arm, even while dead: the monster respawns under the same
        # id, and rescheduling here keeps combat_system from having to know
        # anything about the wander timer.
        eng.schedule(interval, WANDER_ACTION, {"monster_id": monster_id})

        if not monster.alive:
            return []

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

        target = roll.choice(options)
        facing = facing_from_delta(monster.tile, target)
        monster.tile = target
        if facing is not None:
            monster.facing = facing

        return [{
            "type": "monster_moved",
            "monster_id": monster_id,
            "tile": list(target),
            "facing": monster.facing,
        }]

    engine.register_action_handler(WANDER_ACTION, handle_wander)

    # Stagger the first tick per monster so a floor's worth of them doesn't
    # step in unison on the same tick.
    for monster_id, monster in floor.monsters.items():
        interval = interval_for(monster.template_id)
        engine.schedule(roll.randint(1, interval), WANDER_ACTION, {"monster_id": monster_id})
