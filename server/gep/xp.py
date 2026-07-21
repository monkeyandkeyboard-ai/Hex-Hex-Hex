"""XP and levelling system. Works for any skill name -- adding a new skill
is a config change, never a code change. OSRS-style: XP accumulates in the
skill you actively use; levels are a read from the xp_table.json lookup.
"""
from __future__ import annotations

import bisect
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gep.entities import Player


# The xp_table is immutable config, loaded once and never mutated, yet its 2000
# keys were being re-sorted on every level lookup -- and current_level runs on
# every combat swing and gather, while the level/next-level readout runs for
# every skill of every connected player each tick. So the sort is derived once
# and reused.
#
# A single-entry cache keyed on object identity: the live server has exactly
# one xp_table, so every call after the first is a hit. Correctness does not
# rest on the cache -- the `is` check recomputes for any other table (tests
# build several stores), and holding the reference in _cache["table"] keeps the
# comparison sound (a GC'd dict whose id() was recycled can never be `is` the
# one we hold). No memory is retained beyond the most recent table.
_cache: dict = {"table": None, "levels": [], "thresholds": []}


def _sorted_levels(xp_table: dict) -> tuple[list[int], list[float]]:
    """`(levels, thresholds)` ascending -- `thresholds[i]` is the cumulative XP
    to reach `levels[i]`. Cumulative XP rises with level, so thresholds are
    sorted, which is what lets the lookups bisect them."""
    if xp_table is not _cache["table"]:
        levels = sorted(int(k) for k in xp_table)
        _cache["table"] = xp_table
        _cache["levels"] = levels
        _cache["thresholds"] = [xp_table[str(lvl)] for lvl in levels]
    return _cache["levels"], _cache["thresholds"]


def current_level(xp: float, xp_table: dict) -> int:
    """The highest level whose XP threshold is at or below `xp`.

    bisect_right finds the insertion point past every threshold <= xp; one
    before it is the last such index. Below the first threshold this clamps to
    index 0, matching the original binary search's floor at the lowest level.
    """
    levels, thresholds = _sorted_levels(xp_table)
    index = max(bisect.bisect_right(thresholds, xp) - 1, 0)
    return levels[index]


def next_level_threshold(current_xp: float, xp_table: dict) -> float:
    """The first XP threshold strictly above `current_xp`, or the table's
    maximum threshold once `current_xp` is at or past the top level."""
    levels, thresholds = _sorted_levels(xp_table)
    index = bisect.bisect_right(thresholds, current_xp)
    if index < len(thresholds):
        return thresholds[index]
    return thresholds[-1]


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
