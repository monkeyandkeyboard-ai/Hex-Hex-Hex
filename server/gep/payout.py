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
from gep.rewards import KIND_EQUIPMENT


def award_rewards(player, profile_id: str, rewards, rng=None) -> list[dict]:
    """Roll a reward profile and move the result into a player's inventory.

    A full pack is reported as a reward the player didn't receive rather than
    silently voided: the client already logs item_gained, and losing loot
    without being told is worse than not getting it.

    Equipment arrives as a serialized instance string. The resolved stats ride
    along on the event so the client can name and describe it without learning
    to parse the encoding -- the GEP stays the only thing that does.
    """
    events: list[dict] = []
    for reward in rewards.generate(profile_id, rng):
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
