"""Inventory and equipment system.

Registers:
  equip-item    {player_id, inv_slot}         -> move item from inventory to its equipment slot
  unequip-item  {player_id, equip_slot}       -> move item from equipment slot back to inventory

Slot resolution goes through the item registry, so a rolled equipment
instance and a bare registry id are equipped by the same path -- the handler
never learns to parse an instance string itself.

Two-handed items are the one place where equipping touches a second slot:
they occupy main_hand and force off_hand empty. That rule lives here rather
than in the item data because it is about the body, not the item.
"""
from gep.entities import EQUIPMENT_SLOTS, TWO_HAND
from gep.floor_state import FloorState
from gep.tick import TickEngine


def register(
    engine: TickEngine,
    floor: FloorState,
    weapons: dict,
    default_equipment_state: str,
    items,
) -> None:

    def resolve_slot(item_id: str) -> str | None:
        """Which body slot an item goes in, or None if it is not equippable.

        Checks the item registry first and the equipment-state registry
        second: bases are the real item space, while `weapons` holds the
        handful of non-item states (unarmed) that are equipped but never
        dropped.
        """
        declared = items.declared_slot(item_id)
        if declared is None:
            weapon_cfg = weapons.get(item_id)
            declared = weapon_cfg.get("equipment_slot", "main_hand") if weapon_cfg else None
        if declared is None:
            return None
        if declared == TWO_HAND:
            return "main_hand"
        return declared if declared in EQUIPMENT_SLOTS else None

    def is_two_handed(item_id: str | None) -> bool:
        return item_id is not None and items.declared_slot(item_id) == TWO_HAND

    def handle_equip(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        player = floor.players.get(player_id)
        if player is None:
            return [{"type": "error", "reason": "invalid player", "player_id": player_id}]

        try:
            inv_slot = int(intent["inv_slot"])
        except (KeyError, ValueError):
            return [{"type": "error", "reason": "inv_slot required", "player_id": player_id}]

        item = player.inventory.get(inv_slot)
        if not item:
            return [{"type": "error", "reason": "empty inventory slot", "player_id": player_id}]

        item_id = item["item_id"]
        equip_slot = resolve_slot(item_id)
        if equip_slot is None:
            return [{"type": "error", "reason": f"{item_id!r} is not equippable", "player_id": player_id}]

        # Work out everything coming off before anything goes on, so a full
        # pack aborts the swap instead of leaving the player holding neither.
        displaced = []
        current = getattr(player.equipment, equip_slot, None)
        if current:
            displaced.append((equip_slot, current))

        two_handed = is_two_handed(item_id)
        if two_handed:
            off_hand = getattr(player.equipment, "off_hand", None)
            if off_hand:
                displaced.append(("off_hand", off_hand))
        elif equip_slot == "off_hand" and is_two_handed(getattr(player.equipment, "main_hand", None)):
            # Filling the off hand puts the two-hander away.
            displaced.append(("main_hand", getattr(player.equipment, "main_hand")))

        # The equipping item leaves its own inventory slot, so that slot is
        # available to whatever comes off: a straight swap always fits, even
        # at 28/28.
        free_slots = sum(1 for i in range(len(player.inventory_snapshot()))
                         if player.inventory.get(i) is None)
        if item["quantity"] <= 1:
            free_slots += 1
        if len(displaced) > free_slots:
            return [{"type": "error", "reason": "inventory full", "player_id": player_id}]

        item["quantity"] -= 1
        if item["quantity"] <= 0:
            player.inventory[inv_slot] = None

        for slot_name, displaced_id in displaced:
            setattr(player.equipment, slot_name, None)
            player.add_item(displaced_id, 1)

        setattr(player.equipment, equip_slot, item_id)

        # Keep weapon_id in sync for combat.
        if equip_slot == "main_hand":
            player.weapon_id = item_id
        elif any(slot == "main_hand" for slot, _ in displaced):
            player.weapon_id = default_equipment_state

        return [{
            "type": "equipment_update",
            "player_id": player_id,
            "equipment": player.equipment.to_dict(),
            "inventory": player.inventory_snapshot(),
        }]

    def handle_unequip(intent: dict, eng: TickEngine) -> list[dict]:
        player_id = intent.get("player_id")
        player = floor.players.get(player_id)
        if player is None:
            return [{"type": "error", "reason": "invalid player", "player_id": player_id}]

        equip_slot = intent.get("equip_slot")
        if equip_slot not in EQUIPMENT_SLOTS:
            return [{"type": "error", "reason": f"unknown equipment slot {equip_slot!r}", "player_id": player_id}]

        item_id = getattr(player.equipment, equip_slot, None)
        if item_id is None:
            return [{"type": "error", "reason": "slot is empty", "player_id": player_id}]

        if not player.add_item(item_id, 1):
            return [{"type": "error", "reason": "inventory full", "player_id": player_id}]

        setattr(player.equipment, equip_slot, None)
        if equip_slot == "main_hand":
            # Emptying the slot moves the player into the default equipment
            # state rather than into "no weapon" -- combat resolves it through
            # the registry like any other, so there is no unarmed branch.
            player.weapon_id = default_equipment_state

        return [{
            "type": "equipment_update",
            "player_id": player_id,
            "equipment": player.equipment.to_dict(),
            "inventory": player.inventory_snapshot(),
        }]

    engine.register_intent_handler("equip-item", handle_equip)
    engine.register_intent_handler("unequip-item", handle_unequip)
