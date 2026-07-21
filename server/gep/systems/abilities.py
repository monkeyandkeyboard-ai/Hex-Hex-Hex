"""Abilities: the full cast pipeline for players and monsters.

An ability is a *cost*, a *targeting/delivery* mode, and a list of *effects*
(gep/effects.py). The config is normalised to that one shape at load
(config_loader._normalize_abilities), so this module never sees the legacy
top-level damage form -- every ability is `{cost, targeting, effects:[...]}`.

Pipeline for a cast:

    validate -> pay cost, start cooldowns -> (optional cast time) ->
    (optional projectile travel) -> resolve at the impact tile

Resolution walks the effect list. Each effect goes to the pool its kind implies
-- offensive kinds (damage, dot, debuff, stun, root, slow) hit the caster's
enemies, friendly kinds (heal, hot, buff, shield) hit the caster's friends --
so friendly fire stays off without the author choosing sides per effect. Damage
routes through the one combat pipeline (`resolve_attack`) exactly as a swing
does; timed effects are attached for systems/effects.py to tick.

The seams match the rest of the engine: monster casts arrive as scheduled
MONSTER_ABILITY actions (monster_ai decides *when*, this owns resolution),
cast-time resolutions are scheduled named actions guarded by a staleness seq,
and control gating is read from the pure effects module, not wired in here.
"""
import math
import random

from gep import effects
from gep.actions import MONSTER_ABILITY, PLAYER_DEFEATED
from gep.combat import normalize_damage_type, power_from, resolve_attack
from gep.effects import Effect
from gep.floor_state import FloorState
from gep.hexgrid import hex_distance
from gep.payout import monster_death_payout
from gep.tick import TickEngine
from gep.xp import award_xp

USE_ABILITY_INTENT = "use_ability"
CAST_COMPLETE = "ability-cast-complete"   # cast time elapsed -> deliver
ABILITY_IMPACT = "ability-impact"         # projectile arrived -> resolve
CHARGE_REFILL = "ability-charge-refill"   # one charge returns to the pool

OFFENSIVE_KINDS = frozenset({"damage", "dot", "debuff", "stun", "root", "slow",
                             "silence", "disarm", "vulnerability", "taunt"})
FRIENDLY_KINDS = frozenset({"heal", "hot", "buff", "shield", "cleanse",
                            "invulnerable", "fortify", "haste"})


def known_abilities(player, abilities: dict, items) -> set[str]:
    """The ids this player can currently cast: core abilities whose skill
    requirement is met, plus abilities granted by equipped items. Pure -- no
    state is written, so it is safe to call for the snapshot every tick.
    """
    known: set[str] = set()
    for aid, ability in abilities.items():
        if ability["source"] == "core":
            req = ability["requirement"]
            if player.combat_stat(req["skill"]) >= req["level"]:
                known.add(aid)
    for item_id in player.equipment.to_dict().values():
        if item_id is None:
            continue
        granted = items.granted_ability(item_id)
        if granted is not None:
            known.add(granted)
    return known


def register(
    engine: TickEngine,
    floor: FloorState,
    abilities_cfg: dict,
    monsters_cfg: dict,
    combat_constants: dict,
    xp_rates: dict,
    xp_table: dict,
    rewards,
    items,
    on_threat=None,
    conversions: list | None = None,
):
    dealt_rate = xp_rates["combat"]["damage_dealt_rate"]

    # ---- cost / gating -----------------------------------------------------

    def charge_pool(caster, ability_id: str, ability: dict) -> int:
        """Current charges for a charge-gated ability, lazily initialised to
        full the first time it is asked about."""
        pool = caster.ability_charges
        if ability_id not in pool:
            pool[ability_id] = ability["cost"]["charges"]
        return pool[ability_id]

    def gate(caster, ability_id: str, ability: dict, tick: int) -> str | None:
        """Return a rejection reason, or None if the cast may proceed."""
        if effects.is_stunned(caster.active_effects):
            return "stunned"
        if effects.is_silenced(caster.active_effects):
            return "silenced"
        cost = ability["cost"]
        if getattr(caster, "global_cooldown_until", 0) > tick:
            return "global cooldown"
        # Charge-gated abilities gate on the pool (cooldown_ticks is the refill
        # interval, not a hard cooldown); otherwise on the per-ability cooldown.
        if cost["charges"] > 0:
            if charge_pool(caster, ability_id, ability) <= 0:
                return "no charges"
        elif tick < caster.ability_cooldowns.get(ability_id, 0):
            return "on cooldown"
        if caster.mana < cost["mana"]:
            return "not enough mana"
        # HP cost may never be self-lethal.
        if cost["hp"] > 0 and caster.hp <= cost["hp"]:
            return "not enough health"
        return None

    def effective_cooldown(caster, ability: dict) -> int:
        """cooldown_ticks after cooldown_reduction_percent (a non-skill stat, 0
        unless gear grants it). Floors at 0 -- a cooldown cannot go negative."""
        cdr = caster.derived_stat("cooldown_reduction", 0.0)
        base = ability["cooldown_ticks"]
        return base if cdr <= 0 else max(0, round(base * (1.0 - cdr / 100.0)))

    def pay(caster, ability_id: str, ability: dict, eng: TickEngine) -> None:
        cost = ability["cost"]
        caster.mana -= cost["mana"]
        if cost["hp"] > 0:
            caster.hp = max(1.0, caster.hp - cost["hp"])
        cooldown = effective_cooldown(caster, ability)
        if cost["charges"] > 0:
            caster.ability_charges[ability_id] = charge_pool(caster, ability_id, ability) - 1
            eng.schedule(cooldown, CHARGE_REFILL, {
                "caster_id": caster.id, "ability_id": ability_id,
                "maximum": cost["charges"],
            })
        else:
            caster.ability_cooldowns[ability_id] = eng.tick + cooldown
        gcd = ability["global_cooldown_ticks"]
        if gcd > 0 and hasattr(caster, "global_cooldown_until"):
            caster.global_cooldown_until = eng.tick + gcd

    # ---- targeting / resolution --------------------------------------------

    def pools_for(caster_is_player: bool):
        """(enemies, friends) for a caster. Offensive effects land on enemies,
        friendly on friends -- the whole of the no-friendly-fire rule."""
        if caster_is_player:
            return floor.monsters, floor.players
        return floor.players, floor.monsters

    def in_area(pool, impact, radius) -> list:
        return [e for e in pool.values()
                if e.alive and hex_distance(e.tile, impact) <= radius]

    def make_effect(spec: dict, caster, ability_id: str, tick: int) -> Effect:
        """Build a timed Effect from an effect spec, snapshotting any damage off
        the caster's stats now so it never re-scales while it lingers."""
        kind = spec["kind"]
        expires = tick + spec["duration_ticks"]
        if kind == "dot":
            roll = spec["damage_min"] + random.random() * (spec["damage_max"] - spec["damage_min"])
            amount = power_from(caster, spec["power"]) * roll
            return Effect(
                effect_id=ability_id, kind="dot", expires_tick=expires,
                source_id=caster.id, tick_amount=amount,
                interval=spec["interval_ticks"], next_tick=tick + spec["interval_ticks"],
                damage_type=normalize_damage_type(spec["damage_type"], combat_constants),
                train_power=dict(spec["power"]),
            )
        if kind == "hot":
            return Effect(
                effect_id=ability_id, kind="hot", expires_tick=expires,
                source_id=caster.id, tick_amount=spec["magnitude"],
                interval=spec["interval_ticks"], next_tick=tick + spec["interval_ticks"],
            )
        if kind in ("buff", "debuff"):
            return Effect(effect_id=ability_id, kind=kind, expires_tick=expires,
                          source_id=caster.id, magnitude=spec["magnitude"], stat=spec["stat"])
        if kind == "shield":
            return Effect(effect_id=ability_id, kind="shield", expires_tick=expires,
                          source_id=caster.id, absorb_remaining=spec["magnitude"])
        if kind in ("fortify", "vulnerability"):
            return Effect(effect_id=ability_id, kind=kind, expires_tick=expires,
                          source_id=caster.id, magnitude=spec["magnitude"])
        if kind == "slow":
            return Effect(effect_id=ability_id, kind="slow", expires_tick=expires,
                          source_id=caster.id, slow_fraction=spec["slow_fraction"])
        if kind == "haste":
            return Effect(effect_id=ability_id, kind="haste", expires_tick=expires,
                          source_id=caster.id, haste_fraction=spec["haste_fraction"])
        # stun / root / silence / disarm / invulnerable / taunt: plain timed
        # gates. Taunt keeps source_id (the caster) so the AI knows who pulled.
        return Effect(effect_id=ability_id, kind=kind, expires_tick=expires,
                      source_id=caster.id)

    def apply_damage(caster, target, spec: dict, is_player: bool, eng) -> list[dict]:
        """One damage effect against one target, through the combat pipeline."""
        roll = spec["damage_min"] + random.random() * (spec["damage_max"] - spec["damage_min"])
        weapon_damage = power_from(caster, spec["power"]) * roll
        damage_type = normalize_damage_type(spec["damage_type"], combat_constants)
        result = resolve_attack(caster, target, weapon_damage, damage_type, combat_constants)
        events = [result]
        if result["result"] != "hit":
            return events
        if is_player:
            if on_threat is not None:
                on_threat(target, caster.id)
            dmg = result["damage"]
            for stat, coeff in spec["power"].items():
                events.extend(award_xp(caster, stat, dmg * dealt_rate * coeff, xp_table))
            events.extend(award_xp(caster, "precision", dmg * dealt_rate * 0.5, xp_table))
            if not target.alive:
                events.extend(monster_death_payout(
                    caster, target, monsters_cfg, rewards, xp_table, eng,
                    conversions=conversions))
        elif not target.alive:
            eng.schedule(0, PLAYER_DEFEATED, {"player_id": target.id, "killer_id": caster.id})
        return events

    def resolve(caster, is_player: bool, ability: dict, ability_id: str,
                impact, eng: TickEngine) -> list[dict]:
        enemies, friends = pools_for(is_player)
        radius = ability["aoe_radius"]
        results: list[dict] = []
        for spec in ability["effects"]:
            kind = spec["kind"]
            pool = enemies if kind in OFFENSIVE_KINDS else friends
            for target in in_area(pool, impact, radius):
                if kind == "damage":
                    results.extend(apply_damage(caster, target, spec, is_player, eng))
                elif kind == "heal":
                    healed = target.heal(spec["magnitude"])
                    results.append({"type": "effect_applied", "target": target.id,
                                    "effect_id": ability_id, "kind": "heal",
                                    "amount": healed, "target_hp": target.hp})
                elif kind == "cleanse":
                    removed = effects.cleanse(target.active_effects)
                    results.append({"type": "effect_applied", "target": target.id,
                                    "effect_id": ability_id, "kind": "cleanse",
                                    "removed": removed})
                else:
                    effects.apply_timed(target.active_effects,
                                        make_effect(spec, caster, ability_id, eng.tick))
                    if kind in OFFENSIVE_KINDS and is_player and on_threat is not None:
                        on_threat(target, caster.id)
                    results.append({"type": "effect_applied", "target": target.id,
                                    "effect_id": ability_id, "kind": kind})
        return results

    def deliver(caster, is_player: bool, ability: dict, ability_id: str,
                impact, eng: TickEngine) -> list[dict]:
        """Post-cast delivery: fly a projectile if the ability has one, else
        resolve on the spot."""
        speed = ability["projectile_speed_tiles"]
        dist = hex_distance(caster.tile, impact)
        if speed > 0 and dist > 0:
            travel = max(1, math.ceil(dist / speed))
            eng.schedule(travel, ABILITY_IMPACT, {
                "caster_id": caster.id, "is_player": is_player,
                "ability_id": ability_id, "impact": list(impact),
            })
            return [{"type": "projectile", "caster": caster.id, "ability_id": ability_id,
                     "from": list(caster.tile), "to": list(impact), "travel_ticks": travel}]
        events = resolve(caster, is_player, ability, ability_id, impact, eng)
        return [ability_used_event(caster, ability_id, ability, impact, events)] + events

    def ability_used_event(caster, ability_id, ability, impact, results) -> dict:
        return {"type": "ability_used", "caster": caster.id, "ability_id": ability_id,
                "tile": list(impact), "aoe_radius": ability["aoe_radius"], "results": results}

    # ---- player intent ------------------------------------------------------

    def handle_use_ability(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        ability_id = intent.get("ability_id")
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return [{"type": "error", "reason": "invalid caster", "player_id": player_id}]

        ability = abilities_cfg.get(ability_id)
        if ability is None or ability_id not in known_abilities(player, abilities_cfg, items):
            return [{"type": "error", "reason": "unknown ability",
                     "player_id": player_id, "ability_id": ability_id}]

        impact = (intent.get("target_q"), intent.get("target_r"))
        if not floor.is_valid_tile(impact):
            return [{"type": "error", "reason": "target off floor", "player_id": player_id}]
        if hex_distance(player.tile, impact) > ability["range"]:
            return [{"type": "error", "reason": "out of range", "player_id": player_id}]
        reason = gate(player, ability_id, ability, eng.tick)
        if reason is not None:
            return [{"type": "error", "reason": reason, "player_id": player_id}]

        # Commit: pay the cost and start cooldowns before resolving, so the cast
        # is spent whether or not it catches anything.
        pay(player, ability_id, ability, eng)
        player.cast_seq += 1

        cast_ticks = ability["cast_ticks"]
        if cast_ticks > 0:
            player.pending_cast = ability_id
            eng.schedule(cast_ticks, CAST_COMPLETE, {
                "caster_id": player_id, "ability_id": ability_id,
                "impact": list(impact), "seq": player.cast_seq,
            })
            return [{"type": "cast_started", "caster": player_id, "ability_id": ability_id,
                     "cast_ticks": cast_ticks, "tile": list(impact)}]
        return deliver(player, True, ability, ability_id, impact, eng)

    def handle_cast_complete(payload: dict, eng: TickEngine) -> list[dict]:
        player = floor.players.get(payload["caster_id"])
        if player is None or not player.alive:
            return []
        # Superseded (moved / re-cast) or interrupted -> drop silently; the
        # interrupt already announced itself.
        if payload["seq"] != player.cast_seq:
            return []
        player.pending_cast = None
        ability_id = payload["ability_id"]
        ability = abilities_cfg[ability_id]
        # A stun landing during the cast interrupts it.
        if effects.is_stunned(player.active_effects):
            return [{"type": "cast_interrupted", "caster": player.id,
                     "ability_id": ability_id, "reason": "stunned"}]
        return deliver(player, True, ability, ability_id, tuple(payload["impact"]), eng)

    def handle_ability_impact(payload: dict, eng: TickEngine) -> list[dict]:
        is_player = payload["is_player"]
        pool = floor.players if is_player else floor.monsters
        caster = pool.get(payload["caster_id"])
        ability = abilities_cfg.get(payload["ability_id"])
        if ability is None:
            return []
        # The caster may have died mid-flight; the projectile still lands, so
        # fall back to a detached caster only for stat scaling that already got
        # snapshotted at cast time. If the caster is gone entirely, drop.
        if caster is None:
            return []
        impact = tuple(payload["impact"])
        results = resolve(caster, is_player, ability, payload["ability_id"], impact, eng)
        return [ability_used_event(caster, payload["ability_id"], ability, impact, results)] + results

    def handle_charge_refill(payload: dict, eng: TickEngine) -> list[dict]:
        pool = floor.players if payload["caster_id"] in floor.players else floor.monsters
        caster = pool.get(payload["caster_id"])
        if caster is None:
            return []
        aid = payload["ability_id"]
        caster.ability_charges[aid] = min(payload["maximum"],
                                          caster.ability_charges.get(aid, 0) + 1)
        return []

    # ---- monster action -----------------------------------------------------

    def handle_monster_ability(payload: dict, eng: TickEngine) -> list[dict]:
        """A monster's ability, requested by the behaviour system. Gated by the
        same cost/cooldown rules as a player -- a template with a `resource`
        pool runs dry under pressure; one without keeps cooldown-only casting."""
        monster = floor.monsters.get(payload["monster_id"])
        ability = abilities_cfg.get(payload["ability_id"])
        if monster is None or not monster.alive or ability is None:
            return []
        ability_id = payload["ability_id"]
        if gate(monster, ability_id, ability, eng.tick) is not None:
            return []   # can't afford / cooling down / stunned -- silently drop
        pay(monster, ability_id, ability, eng)
        impact = (payload["target_q"], payload["target_r"])
        return deliver(monster, False, ability, ability_id, impact, eng)

    def interrupt_casts(player) -> list[dict]:
        """Handed to movement: choosing to move cancels a cast in flight. Bumps
        the seq so the scheduled completion drops, and names what it cancelled."""
        if getattr(player, "pending_cast", None) is None:
            return []
        aid = player.pending_cast
        player.pending_cast = None
        player.cast_seq += 1
        return [{"type": "cast_interrupted", "caster": player.id,
                 "ability_id": aid, "reason": "moved"}]

    engine.register_intent_handler(USE_ABILITY_INTENT, handle_use_ability)
    engine.register_action_handler(CAST_COMPLETE, handle_cast_complete)
    engine.register_action_handler(ABILITY_IMPACT, handle_ability_impact)
    engine.register_action_handler(CHARGE_REFILL, handle_charge_refill)
    engine.register_action_handler(MONSTER_ABILITY, handle_monster_ability)

    return interrupt_casts
