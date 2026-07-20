"""Reward generation: the single entry point for every payout in the game
(items.md §2; compendium §13.4).

    rewards.generate("GOBLIN_DROPS")
    rewards.generate("WOODEN_CHEST")

Both calls run the same code. The service takes a **reward profile id** and
nothing else -- no entity, no template, no floor, no tick. It cannot tell
whether a monster died, a chest opened, or a quest turned in, which is what
makes it reusable by all three. A caller that has to hand over a monster
template is a caller that a chest cannot imitate.

Layering
--------

    rewards.json   profile  -> which tables, how many rolls   (per source)
    tables.json    table    -> what comes out of one roll     (shared pool)
    ItemRegistry            -> mints the actual item instance (gep/items.py)

The service owns both config layers and the registry, so nothing downstream
passes them around. `combat_system` holds a reference to the service and
knows none of the three.

Two kinds of table exist, and the difference matters:

* `items` tables are weighted lists of template ids. `nothing` is a
  first-class weighted entry rather than a magic empty string: making the
  no-drop outcome carry explicit weight is what lets a designer tune drop
  rate by moving one number, without a separate "drop chance" field that
  could disagree with the table.

* `equipment` tables name no items at all. They state a flat `drop_chance`,
  and on success the base is picked from the item registry weighted by each
  base's own `drop_tier`. Rarity therefore lives in the item files, and a
  table cannot fall out of agreement with the bases it draws from.

Every reward is returned in one shape, `{kind, item_id, quantity}`, so the
caller has no branch on reward type. For equipment, `item_id` is the full
serialized instance string -- already rolled, unique, ready to store.
"""
import random

from gep.rolls import weighted_pick

# Reserved table entry meaning "this roll produced no item".
NOTHING = "nothing"

KIND_ITEMS = "items"
KIND_EQUIPMENT = "equipment"


class RewardError(Exception):
    pass


class RewardService:
    """Rolls reward profiles. Built once at startup by ConfigStore.

    Stateless between calls apart from its config: two callers rolling the
    same profile cannot influence each other, and a caller may pass its own
    seeded rng for reproducible payouts.
    """

    def __init__(self, profiles: dict, tables: dict, items):
        self.profiles = profiles
        self.tables = tables
        self.items = items

    # -- entry point -------------------------------------------------------

    def generate(self, profile_id: str, rng: random.Random | None = None,
                 rarity: float = 1.0) -> list[dict]:
        """Roll a reward profile and return everything it produced.

        Raises on an unknown profile rather than returning nothing: a typo'd
        id that silently pays out empty is indistinguishable from bad luck,
        and would survive testing.

        `rarity` scales equipment drop chance and defaults to 1.0, so a
        caller with no player in hand (a test, a chest opened by script)
        rolls exactly the authored odds.
        """
        profile = self.profiles.get(profile_id)
        if profile is None or profile_id.startswith("_"):
            raise RewardError(f"unknown reward profile {profile_id!r}")

        roll = rng or random
        rewards: list[dict] = []
        for slot in profile.get("slots") or []:
            rewards.extend(self.roll_table(
                slot.get("table"), int(slot.get("rolls", 1)), roll, rarity
            ))
        return rewards

    def roll_table(
        self,
        table_id: str,
        rolls: int = 1,
        rng: random.Random | None = None,
        rarity: float = 1.0,
    ) -> list[dict]:
        """Roll one table directly, bypassing profiles.

        For sources whose drop shape is computed rather than authored -- a
        gathering node scaling rolls with skill, say. Authored sources should
        use `generate()` so their payout stays visible in config.
        """
        table = self.tables.get(table_id)
        if table is None or (isinstance(table_id, str) and table_id.startswith("_")):
            raise RewardError(f"unknown loot table {table_id!r}")

        rolls = max(0, int(rolls))
        if rolls == 0:
            return []

        roll = rng or random
        if table.get("kind", KIND_ITEMS) == KIND_EQUIPMENT:
            return self._roll_equipment(table, rolls, roll, rarity)
        return self._roll_items(table, rolls, roll)

    # -- table kinds -------------------------------------------------------

    @staticmethod
    def _roll_quantity(table: dict, item_id: str, roll: random.Random) -> int:
        bounds = (table.get("quantities") or {}).get(item_id)
        if not bounds:
            return 1
        low, high = int(bounds[0]), int(bounds[1])
        if high < low:
            low, high = high, low
        return roll.randint(max(0, low), max(0, high))

    def _roll_items(self, table: dict, rolls: int, roll: random.Random) -> list[dict]:
        entries = [(entry[0], float(entry[1])) for entry in (table.get("entries") or [])]
        if not entries:
            return []
        if sum(weight for _, weight in entries) <= 0:
            # A table of all-zero weights drops nothing, rather than silently
            # handing out the last entry the way a naive cumulative scan does.
            return []

        rewards = []
        for _ in range(rolls):
            item_id = weighted_pick(roll.random(), entries)
            if item_id == NOTHING:
                continue
            quantity = self._roll_quantity(table, item_id, roll)
            if quantity > 0:
                rewards.append({
                    "kind": KIND_ITEMS, "item_id": item_id, "quantity": quantity,
                })
        return rewards

    def _roll_equipment(self, table: dict, rolls: int, roll: random.Random,
                        rarity: float = 1.0) -> list[dict]:
        if self.items is None:
            raise RewardError("equipment table rolled without an item registry")

        chance = float(table.get("drop_chance", 0.0))
        if chance <= 0:
            return []

        # Rarity raises how often equipment drops, never which base is picked.
        # Weighting the candidates instead would let a high-rarity player pull
        # bases their floor's table never meant to offer, which is depth's job
        # to control, not the player's. Clamped so a chest at drop_chance 1.0
        # cannot exceed certainty and start rolling twice.
        chance = min(1.0, chance * max(0.0, rarity))

        candidates = self.items.drop_candidates(
            slots=table.get("slots"),
            min_tier=int(table.get("min_tier", 1)),
            max_tier=int(table.get("max_tier", 9)),
            types=table.get("types"),
        )
        if not candidates:
            return []

        rewards = []
        for _ in range(rolls):
            if roll.random() >= chance:
                continue
            base_code = weighted_pick(roll.random(), candidates)
            # Equipment never stacks: every instance is a distinct roll, so
            # two of them are two inventory entries even when the base matches.
            rewards.append({
                "kind": KIND_EQUIPMENT,
                "item_id": self.items.roll_instance(base_code, roll),
                "quantity": 1,
            })
        return rewards
