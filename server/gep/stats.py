"""Max HP/Mana/regen formulas, driven by stat_scaling.json (compendium §13.3).
Field names match the real config schema from the prior codebase.
"""


def compute_max_hp(constitution: float, stat_scaling: dict) -> float:
    return stat_scaling["hp_base"] + constitution * stat_scaling["hp_per_con"]


def compute_max_mana(mana_attunement: float, stat_scaling: dict) -> float:
    return stat_scaling["mana_base"] + mana_attunement * stat_scaling["mana_per_mana_attunement"]


def compute_hp_regen(constitution: float, stat_scaling: dict) -> float:
    return stat_scaling["hp_regen_base"] + constitution * stat_scaling["hp_regen_per_con"]


def compute_mana_regen(mana_attunement: float, stat_scaling: dict) -> float:
    return stat_scaling["mana_regen_base"] + mana_attunement * stat_scaling["mana_regen_per_mana_attunement"]


def xp_for_level(level: int, xp_table: dict) -> int:
    """XP required to reach `level`, from the explicit xp_table.json (levels 1-2000)."""
    return xp_table.get(str(level), xp_table[str(max(int(k) for k in xp_table))])


def level_from_xp(xp: float, xp_table: dict) -> int:
    """Current level for a given XP total. O(log n) binary search over the table."""
    keys = sorted(int(k) for k in xp_table)
    lo, hi = 0, len(keys) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if xp_table[str(keys[mid])] <= xp:
            lo = mid
        else:
            hi = mid - 1
    return keys[lo]
