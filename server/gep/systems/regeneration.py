"""Out-of-combat resource regeneration, run once per tick.

Deliberately its own loop. It reads skill levels and writes vitality pools,
and touches nothing else -- no pathfinding, no combat resolution, no queues.
Retuning regen means editing stat_scaling.json and this file; neither can
disturb navigation or damage.

Rates come from the config curves (compendium §13.3):
    hp   = hp_regen_base   + constitution     * hp_regen_per_con
    mana = mana_regen_base + mana_attunement  * mana_regen_per_mana_attunement

Silent by design: regeneration emits no events. Player vitality already
rides the per-tick player_update broadcast, so re-reporting it here would
double the traffic for the same numbers. Monsters carry no client-visible
health display, so their pools stay server-side.
"""
from gep.floor_state import FloorState
from gep.stats import compute_hp_regen, compute_mana_regen
from gep.tick import TickEngine

REGEN_ACTION = "regen-tick"


def register(engine: TickEngine, floor: FloorState, stat_scaling: dict) -> None:
    def regenerate(entity) -> None:
        """Top up one entity's pools, never past the ceiling."""
        if not entity.alive:
            return  # the dead do not heal; respawn restores them outright

        con = entity.combat_stat("constitution")
        entity.hp = min(entity.max_hp, entity.hp + compute_hp_regen(con, stat_scaling))

        # Monsters have no mana pool -- only entities that model one regenerate
        # it, rather than inventing the attribute here.
        if getattr(entity, "max_mana", None):
            attunement = entity.combat_stat("mana_attunement")
            entity.mana = min(
                entity.max_mana,
                entity.mana + compute_mana_regen(attunement, stat_scaling),
            )

    def handle_regen_tick(payload: dict, eng: TickEngine) -> list[dict]:
        for player in floor.players.values():
            regenerate(player)
        for monster in floor.monsters.values():
            regenerate(monster)
        eng.schedule(1, REGEN_ACTION, {})
        return []

    engine.register_action_handler(REGEN_ACTION, handle_regen_tick)
    engine.schedule(1, REGEN_ACTION, {})
