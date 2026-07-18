"""Max HP/Mana formulas, driven entirely by stat_scaling.json (compendium
§13.3). One function, used to build both Player and rolled Monster stats --
never hardcode the formula at either call site.
"""


def compute_max_hp(constitution: float, stat_scaling: dict) -> float:
    return stat_scaling["max_hp_base"] + constitution * stat_scaling["max_hp_per_constitution"]


def compute_max_mana(mana_attunement: float, stat_scaling: dict) -> float:
    return stat_scaling["max_mana_base"] + mana_attunement * stat_scaling["max_mana_per_mana_attunement"]
