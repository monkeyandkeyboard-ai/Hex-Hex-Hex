"""Paying rewards into a player's inventory.

The entity-facing half of reward generation. `gep/rewards.py` decides *what*
a source yields and stays pure; this puts the result somewhere and reports
what happened. Splitting them is what lets the roll logic be tested without
an inventory, and lets a caller roll rewards it does not intend to award
(a preview, a simulation, a drop-rate audit).

Any source can call this -- monster death, a chest, a quest turn-in, a
gathering node. It lives outside `systems/` precisely so that a container
system never has to import from `systems/combat_system.py` to hand a player
an item.
"""
from gep import crossdomain
from gep.rewards import KIND_EQUIPMENT
from gep.xp import award_xp


def award_rewards(player, profile_id: str, rewards, rng=None,
                  conversions: list | None = None) -> list[dict]:
    """Roll a reward profile and move the result into a player's inventory.

    A full pack is reported as a reward the player didn't receive rather than
    silently voided: the client already logs item_gained, and losing loot
    without being told is worse than not getting it.

    Equipment arrives as a serialized instance string. The resolved stats ride
    along on the event so the client can name and describe it without learning
    to parse the encoding -- the GEP stays the only thing that does.

    This is where the player's `item_rarity` is applied, rather than inside
    the roller: `rewards.py` stays pure and knows nothing about who is
    looting, which is what lets a drop-rate audit roll the authored odds by
    simply not passing a player's.
    """
    rarity = crossdomain.resolve(
        player, player.floor_number, conversions or [], "item_rarity", 1.0
    )
    events: list[dict] = []
    for reward in rewards.generate(profile_id, rng, rarity):
        item_id = reward["item_id"]
        quantity = reward["quantity"]
        received = player.add_item(item_id, quantity)
        event = {
            "type": "item_gained" if received else "item_dropped_inventory_full",
            "player_id": player.id,
            "item_id": item_id,
            "quantity": quantity,
            "inventory": player.inventory_snapshot(),
        }
        if reward["kind"] == KIND_EQUIPMENT:
            event["item"] = rewards.items.runtime_stats(item_id)
        events.append(event)
    return events


def monster_death_payout(player, monster, monsters_cfg, rewards, xp_table,
                         eng, conversions=None) -> list[dict]:
    """Everything that follows one killing blow: kill XP, the death event, the
    loot drop, and the respawn timer. Shared so a basic swing
    (systems/combat_system.py) and an ability kill (systems/abilities.py) pay
    out identically -- an AoE that kills three monsters must drop three loot
    tables, exactly as three swings would.

    The killer's auto-attack engagement is *not* touched here: that is combat's
    own bookkeeping about its single target, and an AoE kill of a bystander
    must not end an engagement with a different monster. The caller ends its
    engagement itself when the thing it was locked onto is what died.
    """
    template = monsters_cfg.get(monster.template_id, {})
    events: list[dict] = []
    xp_base = template.get("xp_reward", {}).get("combat_base", 0)
    events.extend(award_xp(player, "constitution", xp_base * 0.1, xp_table))
    events.append({"type": "monster_died", "monster_id": monster.id,
                   "tile": list(monster.tile)})
    reward_table = monster.reward_table_override or template["reward_table"]
    events.extend(award_rewards(player, reward_table, rewards, conversions=conversions))
    respawn_ticks = template.get("respawn_ticks", 60)
    eng.schedule(respawn_ticks, "respawn-monster", {
        "monster_id": monster.id,
        "template_id": monster.template_id,
        "tile": list(monster.tile),
    })
    return events
