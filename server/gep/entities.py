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
from gep.items import is_instance
from gep.statblock import EMPTY, ResolvedStats, build_stats
from gep.stats import compute_max_hp, compute_max_mana

# Slot names are the same vocabulary the item bases use (config/items/), so an
# item's `equipment_slot` names its destination directly and no translation
# table sits between config and the entity.
#
# `two_hand` is deliberately absent: it is a property of an item, not a place
# on the body. A two-handed item occupies main_hand and locks off_hand, which
# the equip handler enforces -- modelling it as a third weapon slot would let
# a player wear a greatsword and a sword at once.
EQUIPMENT_SLOTS = (
    "main_hand", "off_hand", "head", "amulet",
    "torso", "hands", "ring", "legs", "feet", "back",
)

# The `equipment_slot` value an item base uses to mean "both hands".
TWO_HAND = "two_hand"

INVENTORY_SIZE = 28
BAG_SLOTS = 6


@dataclass
class Equipment:
    main_hand: str | None = None
    off_hand: str | None = None
    head: str | None = None
    amulet: str | None = None
    torso: str | None = None
    hands: str | None = None
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
    # Persistent respawn anchor: where death returns this player to, and
    # where they log in. Defaults to the tower entrance; the mechanic for
    # moving it is [OPEN] (compendium §14).
    spawn_floor: int = 1
    spawn_tile: tuple[int, int] = (0, 0)
    # Incremented on every new move intent so previously-scheduled move-step
    # actions from the old path can detect they're stale and drop.
    move_seq: int = 0
    # Same staleness trick for auto-attack: bumped whenever an engagement
    # starts or ends, so a swing queued by a previous engagement drops
    # instead of landing on the new target.
    attack_seq: int = 0
    # Equipment modifiers, aggregated. Rebuilt by `refresh_stats` whenever
    # what the player is wearing changes; never persisted, because it is
    # derived entirely from the equipment ids that are.
    stats: ResolvedStats = field(default_factory=lambda: EMPTY)

    def refresh_stats(self, items) -> None:
        """Re-aggregate equipment after an equip, unequip, or load.

        Must be called at every point equipment changes. That it is a
        separate call rather than a property is a deliberate trade: combat
        resolves stats several times per swing and the registry is not
        reachable from here, so the alternative is threading the item
        registry through `combat_stat` and every one of its callers.
        """
        self.stats = build_stats(self.equipment.to_dict().values(), items)

    def combat_stat(self, skill: str) -> float:
        """The skill level, with equipment modifiers applied.

        The level is the baseline (phase 1); everything gear contributes
        resolves on top of it through the ordered pipeline in statblock.py.
        """
        return self.stats.resolve(skill, self.skills.combat.get(skill, 1))

    def add_item(self, item_id: str, quantity: int) -> bool:
        """Stack with existing slot first, then find an empty slot.
        Returns True if the item fit, False if inventory is full.
        """
        # Rolled equipment never stacks: each instance carries its own
        # modifier rolls, so merging two of them into one slot would destroy
        # the distinct item. Checked structurally rather than by a caller
        # passing a flag, so no call site can forget.
        if not is_instance(item_id):
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
    # Bound from the template so the wire carries what a nameplate needs
    # without the client holding a copy of the monster registry. Both are
    # static per template today; they live on the instance because a future
    # per-spawn elite or level-scaled variant changes them per monster, not
    # per template, and the client should not have to learn that later.
    display_name: str = ""
    level: int = 0
    # Placeholder-sprite render modifiers bound from the static template
    # (config_loader guarantees a complete block).
    visual: dict = field(default_factory=dict)
    # Which spritesheet row/column the client should draw.
    facing: str = "down"
    # Aggro: {player_id: threat_score} for every player that has drawn this
    # monster's attention. Written only by the behaviour system
    # (systems/monster_ai.py) -- combat never reaches in here, it just reports
    # that damage landed.
    threat_table: dict[str, float] = field(default_factory=dict)
    weapon_ready_tick: int = 0
    alive: bool = True
    # Set only for spawns inside a prefab's rarity radius (gep/prefabs.py +
    # gep/spawner.py); combat_system.py prefers this over the template's own
    # reward_table when present. None means "use the template's table".
    reward_table_override: str | None = None

    @property
    def threat_target(self) -> str | None:
        """The player currently being hunted: highest threat score, or None.

        Ties break on the id so the choice is stable across ticks -- a monster
        oscillating between two equally-hated players would re-path every
        cycle and effectively stand still.
        """
        if not self.threat_table:
            return None
        return max(self.threat_table, key=lambda pid: (self.threat_table[pid], pid))

    @threat_target.setter
    def threat_target(self, player_id: str | None) -> None:
        """Kept so callers can drop aggro outright with `= None`. Assigning an
        id makes that player the sole entry rather than merely the top one."""
        self.threat_table = {} if player_id is None else {player_id: 1.0}

    def combat_stat(self, skill: str) -> float:
        return self.stats.get(skill, 0)

    def roll_damage(self) -> float:
        return self.damage_min + random.random() * (self.damage_max - self.damage_min)


def roll_monster(
    monster_id: str, template: dict, stat_scaling: dict,
    reward_table_override: str | None = None,
) -> Monster:
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
        display_name=template["display_name"],
        level=template["level"],
        visual=dict(template.get("visual", {})),
        reward_table_override=reward_table_override,
    )
