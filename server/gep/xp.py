"""XP and levelling system. Works for any skill name -- adding a new skill
is a config change, never a code change. OSRS-style: XP accumulates in the
skill you actively use; levels are a read from the xp_table.json lookup.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gep.entities import Player


def current_level(xp: float, xp_table: dict) -> int:
    """Binary search over the explicit XP table."""
    keys = sorted(int(k) for k in xp_table)
    lo, hi = 0, len(keys) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if xp_table[str(keys[mid])] <= xp:
            lo = mid
        else:
            hi = mid - 1
    return keys[lo]


def award_xp(player: Player, skill: str, amount: float, xp_table: dict) -> list[dict]:
    """Add `amount` XP to `skill` on `player`. Returns any level-up events."""
    events: list[dict] = []
    if skill in player.skills.combat:
        old_level = current_level(player.skills.combat_xp[skill], xp_table)
        player.skills.combat_xp[skill] += amount
        player.skills.combat[skill] = current_level(player.skills.combat_xp[skill], xp_table)
        new_level = player.skills.combat[skill]
    elif skill in player.skills.non_combat:
        old_level = current_level(player.skills.non_combat_xp.get(skill, 0.0), xp_table)
        player.skills.non_combat_xp[skill] = player.skills.non_combat_xp.get(skill, 0.0) + amount
        player.skills.non_combat[skill] = current_level(player.skills.non_combat_xp[skill], xp_table)
        new_level = player.skills.non_combat[skill]
    else:
        # Unknown skill -- initialise it as a non-combat skill
        old_level = 1
        player.skills.non_combat_xp[skill] = player.skills.non_combat_xp.get(skill, 0.0) + amount
        player.skills.non_combat[skill] = current_level(player.skills.non_combat_xp[skill], xp_table)
        new_level = player.skills.non_combat[skill]

    if new_level > old_level:
        events.append({
            "type": "level_up",
            "player_id": player.id,
            "skill": skill,
            "old_level": old_level,
            "new_level": new_level,
        })
    return events


def award_xp_block(player: Player, xp_block: dict, xp_table: dict) -> list[dict]:
    """Award all entries in a resource/action XP block at once.
    xp_block is e.g. {"mineralogy": 10, "strength": 2} from resource config.
    """
    events: list[dict] = []
    for skill, amount in xp_block.items():
        events.extend(award_xp(player, skill, amount, xp_table))
    return events
