"""The stat aggregation layer: how many sources become one number.

Everything that modifies a character -- equipment implicits, rolled affixes,
and later passives and buffs -- lands here as a `(key, value)` pair and is
resolved through one ordered pipeline. Combat asks for a finished number and
never learns where the parts came from.

Why this exists
---------------

Before this module, `Player.combat_stat()` returned the raw skill level and
nothing else, which meant a rolled item's affixes were computed by
`items.runtime_stats()`, shipped to the client to be displayed, and then had
no effect whatsoever on a swing. Gear was cosmetic. This is the layer that
connects the two.

The pipeline
------------

Modifiers do not all combine the same way, and the order they combine in is
the whole design. A modifier's *class* is encoded in its key:

    armor              flat addition
    local_armor        flat addition, applied only to the item carrying it
    strength_percent   additive percentage pool ("increased / reduced")
    strength_more      independent multiplicative scalar ("more / less")

and those classes resolve in a fixed order:

  1. Baseline      the unmodified value: a skill level, or an item's base
                   `armor`. Supplied by the caller as `base`.
  2. Local flat    added to the base of the *individual item* that carries
                   the modifier, before that item joins the global pool.
  3. Global flat   every flat bonus from every source, summed.
  4. Increased     every percentage sharing a target sums into ONE
                   coefficient: `1 + (sum / 100)`. Two +20% rolls are +40%,
                   not 1.2 x 1.2.
  5. More          each source multiplies separately: `x(1 + m/100)` per
                   source. Two +20% "more" rolls are 1.2 x 1.2 = +44%.
                   Keeping these discrete is the entire point of the class;
                   summing them would make it a second increased pool.
  6. Caps and conversions are deliberately NOT here -- see below.

Phases 2 and 3 are separate fields even though `base + local + global` sums
identically to `(base + local) + global` today. The distinction is real, not
decorative: a local increase (phase 4 restricted to one item) scales local
flat and not global flat, which is why `resolve_local` exists as its own
entry point.

Where phase 6 lives
-------------------

Caps, thresholds and damage conversion stay in `combat.py`, where the hit
chance cap and the mitigation soft-cap already are. They are properties of a
combat exchange rather than of a character's sheet, and a cap applied here
would be invisible to the one file that needs to reason about it.

What config exercises today
---------------------------

Only flat modifiers: `config/modifiers.json` rolls flat stat bonuses, and
item implicits may carry `_percent`. The `_more` pool is therefore always
empty in practice. It is implemented and tested anyway because the ordered
pipeline *is* the deliverable -- a phase that only appears once some config
needs it would mean the order was never actually settled.
"""
from dataclasses import dataclass, field

# Modifier classes. The key's shape says which pool a value joins.
FLAT = "flat"
INCREASED = "increased"
MORE = "more"

LOCAL_PREFIX = "local_"

# Checked in order, and every entry must be a suffix no stat name ends in.
# `mana_attunement` and `critical_strike_chance` contain underscores, so
# splitting on the last `_` would mangle them -- this matches whole suffixes
# instead.
_CLASS_SUFFIXES = (
    ("_percent", INCREASED),
    ("_more", MORE),
)


def parse_modifier_key(key: str) -> tuple[str, str, bool]:
    """Split a modifier key into `(target, modifier_class, is_local)`.

    The single place that knows the key grammar. Config validation and
    aggregation both route through it, so a key that validates at load is by
    construction a key that aggregates at runtime -- the two cannot drift
    into disagreeing about what `strength_percent` means.
    """
    is_local = key.startswith(LOCAL_PREFIX)
    body = key[len(LOCAL_PREFIX):] if is_local else key
    for suffix, modifier_class in _CLASS_SUFFIXES:
        # `len(body) > len(suffix)` rejects a bare "_percent" with no target.
        if body.endswith(suffix) and len(body) > len(suffix):
            return body[: -len(suffix)], modifier_class, is_local
    return body, FLAT, is_local


@dataclass
class StatBlock:
    """An accumulated set of modifiers, not yet applied to anything.

    A block is a pure accumulator: it holds pools keyed by target and knows
    how to collapse them against a baseline. It holds no reference to a
    player, an item or the registry, which is what lets one be built from any
    mix of sources and cached on an entity.
    """

    flat: dict[str, float] = field(default_factory=dict)
    increased: dict[str, float] = field(default_factory=dict)
    # A list, not a sum. Each entry multiplies independently (phase 5).
    more: dict[str, list[float]] = field(default_factory=dict)

    def add(self, key: str, value: float) -> None:
        """Route one `(key, value)` pair into its pool.

        Local modifiers are accepted and pooled by their bare target name:
        the caller decides whether a block is a local one (built from a
        single item) or the global one, and a `local_armor` key reaching the
        global block would be a caller bug rather than a key that means
        something different here.
        """
        target, modifier_class, _ = parse_modifier_key(key)
        if modifier_class is MORE:
            self.more.setdefault(target, []).append(float(value))
        elif modifier_class is INCREASED:
            self.increased[target] = self.increased.get(target, 0.0) + float(value)
        else:
            self.flat[target] = self.flat.get(target, 0.0) + float(value)

    def merge(self, other: "StatBlock") -> None:
        """Fold another block into this one, preserving pool semantics --
        increased sums, more concatenates."""
        for target, value in other.flat.items():
            self.flat[target] = self.flat.get(target, 0.0) + value
        for target, value in other.increased.items():
            self.increased[target] = self.increased.get(target, 0.0) + value
        for target, values in other.more.items():
            self.more.setdefault(target, []).extend(values)

    def resolve(self, target: str, base: float = 0.0) -> float:
        """Collapse every pool for `target` against `base`, in phase order."""
        value = base + self.flat.get(target, 0.0)
        value *= 1.0 + self.increased.get(target, 0.0) / 100.0
        for scalar in self.more.get(target, ()):
            value *= 1.0 + scalar / 100.0
        return value

    def targets(self) -> set[str]:
        """Every target any pool in this block touches."""
        return set(self.flat) | set(self.increased) | set(self.more)


@dataclass
class ResolvedStats:
    """A character's finished modifier state.

    Two parts, because local and global modifiers reach a value differently:

      * `local_totals` -- per-item bases already resolved against that item's
        own local modifiers and summed. This is the phase 1+2 result and
        serves as the baseline for targets an item supplies directly, like
        `armor`.
      * `globals` -- every non-local modifier from every source, pooled
        together and applied on top.
    """

    globals: StatBlock = field(default_factory=StatBlock)
    local_totals: dict[str, float] = field(default_factory=dict)

    def resolve(self, target: str, base: float = 0.0) -> float:
        """The finished value for `target`.

        `base` is the character's own baseline (a skill level, or 0 for a
        target that exists only on gear). Item-supplied locals are added to
        it before the global pools apply, so a target can draw on both.
        """
        return self.globals.resolve(target, base + self.local_totals.get(target, 0.0))


# A shared "no modifiers" result, for callers that need to resolve something
# against nothing. Resolving through it returns the baseline unchanged.
#
# NOT a default for entities. `ResolvedStats` is mutable, so handing this
# instance to every player would make one player's modifiers everyone's --
# entities build their own (see `Player.stats`). It is exported for tests and
# for read-only callers, and mutating it is a bug.
EMPTY = ResolvedStats()


def _item_modifiers(item_id: str, registry) -> tuple[dict, dict] | None:
    """`(base, stats)` for an equipped id, or None if it grants nothing.

    Equipment states like `unarmed` are registry entries rather than rolled
    instances and carry no modifiers, so they resolve to None here rather
    than being special-cased by the caller.
    """
    from gep.items import ItemError, is_instance

    if not is_instance(item_id):
        return None
    try:
        resolved = registry.runtime_stats(item_id)
    except ItemError:
        # A stored item referencing a base or modifier that no longer exists.
        # Contributing nothing is the right failure: the alternative is
        # refusing to log the player in over one stale string.
        return None
    return resolved, resolved["stats"]


def build_stats(equipment_ids, registry) -> ResolvedStats:
    """Aggregate every equipped item into one `ResolvedStats`.

    Local modifiers are resolved per item against that item's own base before
    anything is summed -- that separation is why this is a loop over items
    rather than one flat pass over every modifier on the character.
    """
    result = ResolvedStats()

    for item_id in equipment_ids:
        if not item_id:
            continue
        parts = _item_modifiers(item_id, registry)
        if parts is None:
            continue
        base, stats = parts

        local = StatBlock()
        for key, value in stats.items():
            _, _, is_local = parse_modifier_key(key)
            if is_local:
                local.add(key, value)
            else:
                result.globals.add(key, value)

        # Resolve this item's locals against its own base values, then bank
        # the total.
        #
        # `armor` is always banked, even with no local modifier, because an
        # item's raw `armor` field IS its baseline for that target and combat
        # now mitigates with it (combat.py step 5). Resolving armor against its
        # base with an empty local block just returns the base, so this folds
        # plain-armor items in and lets a local_armor% roll scale the base --
        # both through the one ordered pipeline.
        targets = local.targets()
        if float(base.get("armor", 0.0) or 0.0) > 0:
            targets = targets | {"armor"}
        for target in targets:
            item_base = float(base.get(target, 0.0) or 0.0)
            result.local_totals[target] = (
                result.local_totals.get(target, 0.0) + local.resolve(target, item_base)
            )

    return result
