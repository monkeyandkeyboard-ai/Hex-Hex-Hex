"""Player and Monster live state (compendium §13.1, §13.3).

Monster stat schema matches the prior-codebase format:
  skills[skill] = {base, minrandom, maxrandom}  (final = base + roll(minrandom..maxrandom))
  combat = {damage_min, damage_max, speed_ticks}

Both Player and Monster expose .combat_stat(skill) so combat.py never
special-cases "player vs monster" -- one resolution function serves PvE and
(later) PvP alike (§15).
"""
import random
from dataclasses import dataclass, field

from gep.config_loader import COMBAT_SKILLS
from gep.stats import compute_max_hp, compute_max_mana


def xp_to_level(xp: float, xp_table: dict) -> int:
    from gep.stats import level_from_xp
    return level_from_xp(xp, xp_table)


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
    damage_min: float
    damage_max: float
    speed_ticks: int
    weapon_ready_tick: int = 0
    alive: bool = True

    def combat_stat(self, skill: str) -> float:
        return self.stats.get(skill, 0)

    def roll_damage(self) -> float:
        return self.damage_min + random.random() * (self.damage_max - self.damage_min)


def roll_monster(monster_id: str, template: dict, stat_scaling: dict) -> Monster:
    """One instance of a monster template. Stats use the real schema:
    final_stat = skill_block["base"] + roll(minrandom..maxrandom).
    """
    stats = {}
    for skill in COMBAT_SKILLS:
        block = template["skills"][skill]
        base = block["base"]
        lo, hi = block["minrandom"], block["maxrandom"]
        variance = random.uniform(lo, hi) if lo != hi else lo
        stats[skill] = base + variance

    max_hp = compute_max_hp(stats["constitution"], stat_scaling)
    combat = template["combat"]
    return Monster(
        id=monster_id,
        template_id=template["id"],
        floor_number=0,
        tile=(0, 0),
        hp=max_hp,
        max_hp=max_hp,
        stats=stats,
        damage_min=combat["damage_min"],
        damage_max=combat["damage_max"],
        speed_ticks=combat["speed_ticks"],
    )
