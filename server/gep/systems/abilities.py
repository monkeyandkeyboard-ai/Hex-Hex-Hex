"""Abilities: player casts and monster casts, single-target and AoE.

This is the delivery vehicle for area-of-effect. It sits on the combat side of
the monster_ai <-> combat seam (gep/actions.py): monster ability *requests*
arrive as scheduled MONSTER_ABILITY actions exactly like MONSTER_STRIKE, and
this module owns the cooldown gate and resolution -- monster_ai never resolves
damage, and this module never decides where a monster goes.

What a player *knows* is derived, never stored: a core ability is known once its
skill requirement is met; an item ability is known only while an item granting
it is equipped (and item abilities have no other source -- that is the whole
point of the item-only tier). `known_abilities` is a pure function of skills +
equipment, recomputed on demand.

Resolution reuses the single combat pipeline: `resolve_attack` per target,
`power_from` for stat scaling, and `monster_death_payout` for loot/XP/respawn,
so an AoE that kills three monsters pays out exactly as three swings would.
Friendly fire is off: a player ability's target set is monsters only, a
monster ability's is players only.
"""
import random

from gep.actions import MONSTER_ABILITY, PLAYER_DEFEATED
from gep.combat import normalize_damage_type, power_from, resolve_attack
from gep.floor_state import FloorState
from gep.hexgrid import hex_distance
from gep.payout import monster_death_payout
from gep.tick import TickEngine
from gep.xp import award_xp

USE_ABILITY_INTENT = "use_ability"


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
) -> None:
    def roll_damage(ability, caster) -> tuple[float, str]:
        """The pre-mitigation damage of one hit and its type. `power` scales off
        the caster's stats the same way a weapon class does; the ability's
        damage_min/max is a multiplier on that ceiling (weapon model)."""
        power = power_from(caster, ability["power"])
        roll = ability["damage_min"] + random.random() * (
            ability["damage_max"] - ability["damage_min"])
        damage_type = normalize_damage_type(ability["damage_type"], combat_constants)
        return power * roll, damage_type

    def targets_in_area(pool, impact, radius) -> list:
        """Live entities from `pool` within `radius` of the impact tile. radius
        0 collapses to just whatever stands on the tile."""
        return [
            e for e in pool.values()
            if e.alive and hex_distance(e.tile, impact) <= radius
        ]

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
        if eng.tick < player.ability_cooldowns.get(ability_id, 0):
            return [{"type": "error", "reason": "on cooldown", "player_id": player_id}]
        if player.mana < ability["mana_cost"]:
            return [{"type": "error", "reason": "not enough mana", "player_id": player_id}]

        # Commit: pay the cost and start the cooldown before resolving, so the
        # cast is spent whether or not it happened to catch anything.
        player.mana -= ability["mana_cost"]
        player.ability_cooldowns[ability_id] = eng.tick + ability["cooldown_ticks"]

        dealt_rate = xp_rates["combat"]["damage_dealt_rate"]
        results: list[dict] = []
        # Hostiles-only: a player ability hits monsters, never other players.
        for monster in targets_in_area(floor.monsters, impact, ability["aoe_radius"]):
            weapon_damage, damage_type = roll_damage(ability, player)
            result = resolve_attack(player, monster, weapon_damage, damage_type,
                                    combat_constants)
            results.append(result)
            if result["result"] != "hit":
                continue
            if on_threat is not None:
                on_threat(monster, player_id)
            dmg = result["damage"]
            # Train the stats the ability scales off (a fire spell trains
            # arcana, a cleave trains strength) plus precision, mirroring how a
            # swing credits its driving stat -- an AoE hitting three monsters
            # credits three hits' worth, exactly as three swings would.
            for stat, coeff in ability["power"].items():
                results.extend(award_xp(player, stat, dmg * dealt_rate * coeff, xp_table))
            results.extend(award_xp(player, "precision", dmg * dealt_rate * 0.5, xp_table))
            if not monster.alive:
                results.extend(monster_death_payout(
                    player, monster, monsters_cfg, rewards, xp_table, eng,
                    conversions=conversions))

        return [{
            "type": "ability_used",
            "caster": player_id,
            "ability_id": ability_id,
            "tile": list(impact),
            "aoe_radius": ability["aoe_radius"],
            "results": results,
        }] + results

    def handle_monster_ability(payload: dict, eng: TickEngine) -> list[dict]:
        """A monster's ability, requested by the behaviour system. Cooldown is
        the only gate -- monsters have no mana economy this version."""
        monster = floor.monsters.get(payload["monster_id"])
        ability = abilities_cfg.get(payload["ability_id"])
        if monster is None or not monster.alive or ability is None:
            return []
        ability_id = payload["ability_id"]
        if eng.tick < monster.ability_cooldowns.get(ability_id, 0):
            return []   # still cooling down -- silently drop, as MONSTER_STRIKE does
        monster.ability_cooldowns[ability_id] = eng.tick + ability["cooldown_ticks"]

        impact = (payload["target_q"], payload["target_r"])
        results: list[dict] = []
        # Hostiles-only: a monster ability hits players, never other monsters.
        for player in targets_in_area(floor.players, impact, ability["aoe_radius"]):
            weapon_damage, damage_type = roll_damage(ability, monster)
            result = resolve_attack(monster, player, weapon_damage, damage_type,
                                    combat_constants)
            results.append(result)
            if result["result"] == "hit" and not player.alive:
                eng.schedule(0, PLAYER_DEFEATED, {
                    "player_id": player.id,
                    "killer_id": monster.id,
                })

        return [{
            "type": "ability_used",
            "caster": payload["monster_id"],
            "ability_id": ability_id,
            "tile": list(impact),
            "aoe_radius": ability["aoe_radius"],
            "results": results,
        }] + results

    engine.register_intent_handler(USE_ABILITY_INTENT, handle_use_ability)
    engine.register_action_handler(MONSTER_ABILITY, handle_monster_ability)
