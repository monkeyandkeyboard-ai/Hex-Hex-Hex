"""Player and Monster live state (compendium §13.1, §13.3). Both expose the
same six-combat-skill shape so combat.py never special-cases "player" vs
"monster" -- one resolution function serves PvE and (later) PvP alike.
"""
from dataclasses import dataclass, field

from gep.config_loader import COMBAT_SKILLS
from gep.prng import Mulberry32
from gep.stats import compute_max_hp


def xp_to_level(xp: float, base_xp: float, exponent: float, max_level: int) -> int:
    level = 1
    while level < max_level and xp >= base_xp * (level ** exponent):
        level += 1
    return level


@dataclass
class Skills:
    combat: dict[str, float] = field(default_factory=lambda: {s: 1 for s in COMBAT_SKILLS})
    non_combat: dict[str, float] = field(default_factory=dict)
    combat_xp: dict[str, float] = field(default_factory=lambda: {s: 0.0 for s in COMBAT_SKILLS})
    non_combat_xp: dict[str, float] = field(default_factory=dict)


@dataclass
class Player:
    id: str
    name: str
    tower_id: str
    floor_number: int
    tile: tuple[int, int]
    hp: float
    max_hp: float
    mana: float
    max_mana: float
    weapon_id: str
    skills: Skills = field(default_factory=Skills)
    weapon_ready_tick: int = 0
    alive: bool = True

    def combat_stat(self, skill: str) -> float:
        return self.skills.combat.get(skill, 1)


@dataclass
class Monster:
    id: str
    template_id: str
    floor_number: int
    tile: tuple[int, int]
    hp: float
    max_hp: float
    stats: dict[str, float]
    weapon_base_damage: float
    damage_type: str
    weapon_ready_tick: int = 0
    alive: bool = True

    def combat_stat(self, skill: str) -> float:
        return self.stats.get(skill, 0)


def roll_monster(monster_id: str, template: dict, rng: Mulberry32, stat_scaling: dict) -> Monster:
    """One instance of a monster template, rolled per the compendium's
    'base level + per-stat variance range' pattern (§13.3) -- the same
    weighted/ranged-roll idiom used for visual parts and loot.
    """
    stats = {}
    for skill in COMBAT_SKILLS:
        rng_range = template["stats"][skill]
        lo, hi = rng_range["min"], rng_range["max"]
        stats[skill] = lo if lo == hi else lo + rng.next_float() * (hi - lo)

    max_hp = compute_max_hp(stats["constitution"], stat_scaling)
    return Monster(
        id=monster_id,
        template_id=template["id"],
        floor_number=0,
        tile=(0, 0),
        hp=max_hp,
        max_hp=max_hp,
        stats=stats,
        weapon_base_damage=template["weapon_base_damage"],
        damage_type=template["damage_type"],
    )
