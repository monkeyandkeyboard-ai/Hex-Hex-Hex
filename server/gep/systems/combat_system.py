"""Combat system. Registers attack intent onto the TickEngine.

Attack model:
- Player sends {intent_type:"attack", player_id, target_id}
- Validates target is a live monster on the same floor
- The player turns to face the target, and stays engaged: every weapon cycle
  the server swings again on its own, with no further input
- Awards combat XP based on damage dealt (damage_dealt_rate from xp_rates)
- Handles monster death: emits death event, schedules respawn

Auto-combat ends when the target dies, the player dies, the target leaves the
floor, or the player issues a movement command. That last one arrives through
the `break_engagement` callback handed to the movement system -- movement
never touches combat state itself, the same way combat never touches the
monster threat slot (see systems/monster_ai.py).

Swing cadence is the weapon's `cooldown_ticks`, not one swing per tick: the
weapon config already owns that number and auto-combat must not smuggle in a
second, faster rate.
"""
from gep.combat import normalize_damage_type, resolve_attack
from gep.floor_state import FloorState
from gep.actions import MONSTER_STRIKE, PLAYER_DEFEATED
from gep.hexgrid import facing_toward
from gep.payout import award_rewards
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
    rewards,
    on_threat=None,
) -> None:
    def face_target(player, monster) -> list[dict]:
        """Turn the attacker to look at what they are hitting.

        Broadcast as a position_update on the player's current tile: the
        client already applies facing from that event, and reporting a turn
        as a zero-distance move means the interpolator treats it as a turn
        rather than starting a glide.
        """
        facing = facing_toward(player.tile, monster.tile)
        if facing is None or facing == player.facing:
            return []
        player.facing = facing
        return [{
            "type": "position_update",
            "player_id": player.id,
            "tile": list(player.tile),
            "facing": player.facing,
        }]

    def end_engagement(player) -> None:
        """Stop auto-attacking. Bumping the sequence invalidates any swing
        already sitting in the queue, so it can't land after disengaging."""
        if player.combat_target is not None:
            player.combat_target = None
            player.attack_seq += 1

    def swing(player, monster, eng: TickEngine) -> list[dict]:
        """One resolved attack plus everything that follows from it."""
        weapon = weapons.get(player.weapon_id)
        if weapon is None:
            return [{"type": "error", "reason": f"unknown weapon {player.weapon_id!r}"}]

        target_id = monster.id
        player_id = player.id
        events = face_target(player, monster)

        # Flat baseline: no per-swing roll. Variance in the outcome comes from
        # the evasion and hit checks, not from the damage number.
        weapon_damage = weapon["base_power"]
        damage_type = normalize_damage_type(weapon.get("damage_type"), combat_constants)

        result = resolve_attack(player, monster, weapon_damage, damage_type, combat_constants)
        events.append(result)

        if result["result"] == "hit":
            # Report the hit to whatever owns behaviour and move on. This
            # module deliberately knows nothing about what threat does -- see
            # systems/monster_ai.py for why the seam is a callback.
            if on_threat is not None:
                on_threat(monster, player_id)

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
                events.extend(award_rewards(player, template["reward_table"], rewards))
                respawn_ticks = template.get("respawn_ticks", 60)
                eng.schedule(respawn_ticks, "respawn-monster", {
                    "monster_id": target_id,
                    "template_id": monster.template_id,
                    "tile": list(monster.tile),
                })
                # Nothing left to auto-attack.
                end_engagement(player)

        player.weapon_ready_tick = eng.tick + weapon["cooldown_ticks"]

        # Queue the next swing for the moment the weapon comes back up. The
        # seq check in the handler drops this if the engagement ended first.
        if player.combat_target == target_id:
            eng.schedule(weapon["cooldown_ticks"], "auto-attack", {
                "player_id": player_id,
                "target_id": target_id,
                "seq": player.attack_seq,
            })
        return events

    def handle_attack(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        target_id = intent.get("target_id")

        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return [{"type": "error", "reason": "invalid attacker", "player_id": player_id}]

        monster = floor.monsters.get(target_id)
        if monster is None or not monster.alive:
            return [{"type": "error", "reason": "invalid target", "player_id": player_id}]

        # Re-targeting mid-engagement: invalidate the queued swing from the
        # old target before adopting the new one.
        if player.combat_target != target_id:
            end_engagement(player)
        player.combat_target = target_id

        events = [{"type": "engagement_started", "player_id": player_id, "target_id": target_id}]

        if eng.tick < player.weapon_ready_tick:
            # Mid-cooldown. Face the target now and let the queued swing land
            # when the weapon is ready -- clicking a target while on cooldown
            # should start the engagement, not fail.
            events.extend(face_target(player, monster))
            eng.schedule(max(1, player.weapon_ready_tick - eng.tick), "auto-attack", {
                "player_id": player_id,
                "target_id": target_id,
                "seq": player.attack_seq,
            })
            return events

        events.extend(swing(player, monster, eng))
        return events

    def handle_auto_attack(payload: dict, eng: TickEngine) -> list[dict]:
        player_id = payload["player_id"]
        player = floor.players.get(player_id)
        if player is None or not player.alive:
            return []

        # Superseded by a newer engagement (or by disengaging entirely).
        if payload.get("seq", 0) != player.attack_seq:
            return []
        if player.combat_target != payload["target_id"]:
            return []

        monster = floor.monsters.get(payload["target_id"])
        if monster is None or not monster.alive:
            end_engagement(player)
            return [{"type": "engagement_ended", "player_id": player_id, "reason": "target gone"}]

        if eng.tick < player.weapon_ready_tick:
            # Shouldn't normally happen, but never busy-swing: wait it out.
            eng.schedule(max(1, player.weapon_ready_tick - eng.tick), "auto-attack", payload)
            return []

        return swing(player, monster, eng)

    def handle_monster_strike(payload: dict, eng: TickEngine) -> list[dict]:
        """A monster's attack, requested by the behaviour system.

        Behaviour asks for this whenever its target is in range; the pacing
        gate lives here, so a monster cannot swing faster than its configured
        speed no matter how often it asks.
        """
        monster = floor.monsters.get(payload["monster_id"])
        player = floor.players.get(payload["player_id"])
        if monster is None or not monster.alive:
            return []
        if player is None or not player.alive:
            return []
        if eng.tick < monster.weapon_ready_tick:
            return []   # still on cooldown -- silently drop the request

        template = monsters_cfg.get(monster.template_id, {})
        damage_type = normalize_damage_type(
            template.get("combat", {}).get("damage_type"), combat_constants)

        # Attacker first, target second: the monster is swinging at the player.
        result = resolve_attack(monster, player, monster.roll_damage(), damage_type,
                                combat_constants)
        events = [result]

        # Cooldown applies to every outcome -- hit, miss and dodge alike.
        monster.weapon_ready_tick = eng.tick + monster.speed_ticks

        if result["result"] == "hit" and not player.alive:
            # Defeat handling belongs to the respawn system, not here. Combat
            # reports the death; what happens to the player is decided there.
            eng.schedule(0, PLAYER_DEFEATED, {
                "player_id": player.id,
                "killer_id": monster.id,
            })
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
                 "visual": monster.visual, "facing": monster.facing}]

    engine.register_intent_handler("attack", handle_attack)
    engine.register_action_handler("auto-attack", handle_auto_attack)
    engine.register_action_handler(MONSTER_STRIKE, handle_monster_strike)
    engine.register_action_handler("respawn-monster", handle_respawn_monster)

    def break_engagement(player) -> list[dict]:
        """Handed to the movement system: issuing a move drops auto-combat.

        Movement calls this and knows nothing else about combat -- it never
        reads or writes combat_target itself.
        """
        if player.combat_target is None:
            return []
        end_engagement(player)
        return [{"type": "engagement_ended", "player_id": player.id, "reason": "moved"}]

    return break_engagement
