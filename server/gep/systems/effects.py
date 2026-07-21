"""The per-tick sweep that drives timed effects (gep/effects.py).

Structurally a sibling of regeneration: one recurring action walks every entity
on the floor and advances its active effects -- periodic damage/heal land, and
anything past its lifetime is dropped and announced. It owns nothing about how
effects are *created* (systems/abilities.py builds and attaches them); it owns
what happens to them over time.

A dot credits its source exactly as a swing would: the same damage_dealt_rate
XP and, when a tick lands the killing blow, the same monster_death_payout. So a
poison that finishes a monster off drops loot identically to the swing that
would have. A source that has since left the floor simply goes uncredited --
the damage still lands, matching how a fired projectile does not need its
shooter to still be present.
"""
from gep.actions import PLAYER_DEFEATED
from gep.floor_state import FloorState
from gep.payout import monster_death_payout
from gep.tick import TickEngine
from gep.xp import award_xp

EFFECT_TICK = "effect-tick"


def register(
    engine: TickEngine,
    floor: FloorState,
    xp_rates: dict,
    xp_table: dict,
    monsters_cfg: dict,
    rewards,
    conversions: list | None = None,
) -> None:
    dealt_rate = xp_rates["combat"]["damage_dealt_rate"]

    def credit_dot(effect, damage: float) -> list[dict]:
        """Award the source player XP for a dot tick, mirroring a swing: the
        stats the dot scales off, plus a half share to precision. No-op when the
        source is a monster or has left the floor."""
        source = floor.players.get(effect.source_id)
        if source is None or not source.alive:
            return []
        events: list[dict] = []
        for stat, coeff in effect.train_power.items():
            events.extend(award_xp(source, stat, damage * dealt_rate * coeff, xp_table))
        events.extend(award_xp(source, "precision", damage * dealt_rate * 0.5, xp_table))
        return events

    def on_target_death(target, effect, eng: TickEngine) -> list[dict]:
        """A periodic tick reduced a target to zero. Route the death the same
        way combat does: a monster pays out to its killer, a player is handed to
        the defeat lifecycle. Clear the corpse's remaining effects so a second
        dot cannot pay out again."""
        events: list[dict] = []
        if target in floor.monsters.values():
            source = floor.players.get(effect.source_id)
            if source is not None:
                events.extend(monster_death_payout(
                    source, target, monsters_cfg, rewards, xp_table, eng,
                    conversions=conversions))
        else:
            eng.schedule(0, PLAYER_DEFEATED, {
                "player_id": target.id,
                "killer_id": effect.source_id,
            })
        target.active_effects.clear()
        return events

    def process(entity, tick: int, eng: TickEngine) -> list[dict]:
        if not entity.active_effects:
            return []
        events: list[dict] = []
        for effect in list(entity.active_effects):
            # Periodic damage/heal, at most once per interval, only up to expiry
            # and only on a living entity.
            if entity.alive and effect.kind in ("dot", "hot") and tick >= effect.next_tick:
                if effect.kind == "dot":
                    entity.take_damage(effect.tick_amount)
                    events.append({"type": "effect_tick", "target": entity.id,
                                   "effect_id": effect.effect_id, "kind": "dot",
                                   "amount": effect.tick_amount,
                                   "target_hp": entity.hp, "target_alive": entity.alive})
                    events.extend(credit_dot(effect, effect.tick_amount))
                    if not entity.alive:
                        events.extend(on_target_death(entity, effect, eng))
                        return events   # corpse's effects were cleared
                else:
                    healed = entity.heal(effect.tick_amount)
                    events.append({"type": "effect_tick", "target": entity.id,
                                   "effect_id": effect.effect_id, "kind": "hot",
                                   "amount": healed, "target_hp": entity.hp})
                effect.next_tick += effect.interval

            # Expiry, and depleted shields (absorbed their whole pool early).
            depleted = effect.kind == "shield" and effect.absorb_remaining <= 0
            if tick >= effect.expires_tick or depleted:
                if effect in entity.active_effects:
                    entity.active_effects.remove(effect)
                events.append({"type": "effect_expired", "target": entity.id,
                               "effect_id": effect.effect_id, "kind": effect.kind})
        return events

    def handle_effect_tick(payload: dict, eng: TickEngine) -> list[dict]:
        events: list[dict] = []
        # Snapshot both collections: a dot death can schedule a respawn/defeat
        # but never mutates these dicts mid-sweep.
        for entity in list(floor.players.values()) + list(floor.monsters.values()):
            events.extend(process(entity, eng.tick, eng))
        eng.schedule(1, EFFECT_TICK, {})
        return events

    engine.register_action_handler(EFFECT_TICK, handle_effect_tick)
    engine.schedule(1, EFFECT_TICK, {})
