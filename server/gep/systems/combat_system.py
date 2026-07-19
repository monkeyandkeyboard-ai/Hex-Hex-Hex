"""Combat system. Registers attack intent onto the TickEngine.

Attack model:
- Player sends {intent_type:"attack", player_id, target_id}
- Validates target is a live monster on the same floor, player weapon is ready
- Resolves combat immediately (same tick), schedules weapon cooldown
- Awards combat XP based on damage dealt (damage_dealt_rate from xp_rates)
- Handles monster death: emits death event, schedules respawn
"""
import random

from gep.combat import resolve_attack
from gep.floor_state import FloorState
from gep.tick import TickEngine
from gep.xp import award_xp


def register(
    engine: TickEngine,
    floor: FloorState,
    weapons: dict,
    monsters_cfg: dict,
    combat_constants: dict,
    xp_rates: dict,
    xp_table: dict,
    stat_scaling: dict,
) -> None:
    def handle_attack(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        target_id = intent.get("target_id")

        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return [{"type": "error", "reason": "invalid attacker", "player_id": player_id}]

        monster = floor.monsters.get(target_id)
        if monster is None or not monster.alive:
            return [{"type": "error", "reason": "invalid target", "player_id": player_id}]

        if eng.tick < player.weapon_ready_tick:
            return [{"type": "error", "reason": "weapon not ready", "player_id": player_id,
                     "ready_at_tick": player.weapon_ready_tick}]

        weapon = weapons.get(player.weapon_id)
        if weapon is None:
            return [{"type": "error", "reason": f"unknown weapon {player.weapon_id!r}"}]

        weapon_damage = weapon["damage_min"] + random.random() * (weapon["damage_max"] - weapon["damage_min"])
        damage_type = weapon.get("type", "physical").lower()
        if damage_type not in ("physical", "arcana", "elemental"):
            damage_type = "physical"

        result = resolve_attack(player, monster, weapon_damage, damage_type, combat_constants)
        events = [result]

        if result["result"] == "hit":
            dmg = result["damage"]
            dealt_rate = xp_rates["combat"]["damage_dealt_rate"]
            events.extend(award_xp(player, "strength", dmg * dealt_rate, xp_table))
            events.extend(award_xp(player, "precision", dmg * dealt_rate * 0.5, xp_table))

            if not monster.alive:
                template = monsters_cfg.get(monster.template_id, {})
                xp_base = template.get("xp_reward", {}).get("combat_base", 0)
                events.extend(award_xp(player, "constitution", xp_base * 0.1, xp_table))
                events.append({"type": "monster_died", "monster_id": target_id,
                               "tile": list(monster.tile)})
                respawn_ticks = template.get("respawn_ticks", 60)
                eng.schedule(respawn_ticks, "respawn-monster", {
                    "monster_id": target_id,
                    "template_id": monster.template_id,
                    "tile": list(monster.tile),
                })

        player.weapon_ready_tick = eng.tick + weapon["speed_ticks"]
        return events

    def handle_respawn_monster(payload: dict, eng: TickEngine) -> list[dict]:
        from gep.entities import roll_monster
        monster_id = payload["monster_id"]
        template_id = payload["template_id"]
        tile = tuple(payload["tile"])
        template = monsters_cfg.get(template_id)
        if template is None:
            return []
        monster = roll_monster(monster_id, template, stat_scaling)
        monster.floor_number = floor.layout.floor_number
        monster.tile = tile
        floor.monsters[monster_id] = monster
        return [{"type": "monster_spawned", "monster_id": monster_id,
                 "template_id": template_id, "tile": list(tile),
                 "visual": monster.visual}]

    engine.register_intent_handler("attack", handle_attack)
    engine.register_action_handler("respawn-monster", handle_respawn_monster)
