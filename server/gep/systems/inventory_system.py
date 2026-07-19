"""Inventory and equipment system.

Registers:
  equip-item    {player_id, inv_slot}         -> move item from inventory to its equipment slot
  unequip-item  {player_id, equip_slot}       -> move item from equipment slot back to inventory
"""
from gep.entities import EQUIPMENT_SLOTS
from gep.floor_state import FloorState
from gep.tick import TickEngine


def register(
    engine: TickEngine,
    floor: FloorState,
    weapons: dict,
    default_equipment_state: str,
) -> None:

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

        # Determine equipment slot from weapon config; other item types added as config grows
        equip_slot = None
        weapon_cfg = weapons.get(item_id)
        if weapon_cfg:
            equip_slot = weapon_cfg.get("equipment_slot", "main_hand")

        if equip_slot is None or equip_slot not in EQUIPMENT_SLOTS:
            return [{"type": "error", "reason": f"{item_id!r} is not equippable", "player_id": player_id}]

        # Swap currently equipped item back to inventory if present
        currently_equipped = getattr(player.equipment, equip_slot, None)
        if currently_equipped:
            player.add_item(currently_equipped, 1)

        # Equip
        setattr(player.equipment, equip_slot, item_id)
        item["quantity"] -= 1
        if item["quantity"] <= 0:
            player.inventory[inv_slot] = None

        # Keep weapon_id in sync for combat system
        if equip_slot == "main_hand":
            player.weapon_id = item_id

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
