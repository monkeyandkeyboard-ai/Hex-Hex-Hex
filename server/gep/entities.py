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

EQUIPMENT_SLOTS = (
    "main_hand", "off_hand", "helmet", "amulet",
    "body", "gloves", "ring", "legs", "feet", "back",
)

INVENTORY_SIZE = 28
BAG_SLOTS = 6


@dataclass
class Equipment:
    main_hand: str | None = None
    off_hand: str | None = None
    helmet: str | None = None
    amulet: str | None = None
    body: str | None = None
    gloves: str | None = None
    ring: str | None = None
    legs: str | None = None
    feet: str | None = None
    back: str | None = None

    def to_dict(self) -> dict:
        return {s: getattr(self, s) for s in EQUIPMENT_SLOTS}

    @classmethod
    def from_dict(cls, d: dict) -> "Equipment":
        return cls(**{s: d.get(s) for s in EQUIPMENT_SLOTS})


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
    equipment: Equipment = field(default_factory=Equipment)
    # slot_index -> {item_id, quantity} | None  (28 base slots)
    inventory: dict[int, dict | None] = field(default_factory=dict)
    # 6 bag slots hold bag items that expand inventory
    bag_slots: list[str | None] = field(default_factory=lambda: [None] * BAG_SLOTS)
    weapon_ready_tick: int = 0
    alive: bool = True
    # Which spritesheet column the client draws, updated as the player walks
    # and whenever they turn to face something they are attacking.
    facing: str = "down"
    # Auto-combat: monster id being attacked every weapon cycle until the
    # target dies or the player issues a movement command. None when idle.
    combat_target: str | None = None
    # Incremented on every new move intent so previously-scheduled move-step
    # actions from the old path can detect they're stale and drop.
    move_seq: int = 0
    # Same staleness trick for auto-attack: bumped whenever an engagement
    # starts or ends, so a swing queued by a previous engagement drops
    # instead of landing on the new target.
    attack_seq: int = 0

    def combat_stat(self, skill: str) -> float:
        return self.skills.combat.get(skill, 1)

    def add_item(self, item_id: str, quantity: int) -> bool:
        """Stack with existing slot first, then find an empty slot.
        Returns True if the item fit, False if inventory is full.
        """
        # Try to stack with an existing slot
        for i in range(INVENTORY_SIZE):
            slot = self.inventory.get(i)
            if slot and slot["item_id"] == item_id:
                slot["quantity"] += quantity
                return True
        # Find an empty slot
        for i in range(INVENTORY_SIZE):
            if self.inventory.get(i) is None:
                self.inventory[i] = {"item_id": item_id, "quantity": quantity}
                return True
        return False

    def inventory_snapshot(self) -> list:
        """28-element list, each None or {item_id, quantity}."""
        return [self.inventory.get(i) for i in range(INVENTORY_SIZE)]


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
    # Placeholder-sprite render modifiers bound from the static template
    # (config_loader guarantees a complete block).
    visual: dict = field(default_factory=dict)
    # Which spritesheet row/column the client should draw.
    facing: str = "down"
    # Aggro anchor: player id this monster is hunting, or None when passive.
    # Written only by the behaviour system (systems/monster_ai.py) -- combat
    # never reaches in here, it just reports that damage landed.
    threat_target: str | None = None
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
        visual=dict(template.get("visual", {})),
    )
