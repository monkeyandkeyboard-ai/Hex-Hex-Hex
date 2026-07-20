"""Cross-domain conversion: the outer loops feeding the stat pipeline.

Gathering proficiency and floor depth are progression the player earns
outside combat. This module is the one place they are allowed to turn into
character power, and it is deliberately narrow about what kind.

Sideways, never damage
----------------------

The design constraint is that non-combat investment must never scale
offensive output. A game where mining raises your damage is a game where
mining is a prerequisite for raiding, and the chore is not optional however
it is framed. So conversions may only target the *utility* vocabulary below,
and `validate_conversions` rejects any config naming a combat stat.

That is enforced at load rather than left as a convention, because a
convention is exactly what gets edited away eighteen months later by someone
who only wants "a small strength bonus from smithing". The forbidden set is
passed in by the caller rather than restated here, so it cannot drift from
the real list of combat stats.

Declared vs. consumed
---------------------

`UTILITY_STATS` maps each stat to the module that reads it. An entry mapped
to None is declared but has no consumer yet -- the mechanic it belongs to
does not exist. Config may not wire a conversion into one, and the error
says which system is missing.

This is the point of the split. `skills.json` used to carry an `influences`
table that nothing read, which looked exactly like working behaviour and was
not. A vocabulary that documents intent is useful; a vocabulary that lets
config wire power into a system that will not read it is the same trap with
a new name.

Why this is computed fresh rather than cached
---------------------------------------------

Equipment modifiers are cached on the player (see `Player.refresh_stats`)
because combat resolves them several times per swing and equipment changes
at a handful of known points. Cross-domain inputs are the opposite: skill
levels move on every XP award and floor depth on every staircase, while the
utility stats they feed are read once per gather, per move, or per drop.
Caching those would multiply the number of places a stale block could be
served, to save arithmetic on a cold path. So they are built at the point of
use and there is no staleness to get wrong.
"""
from gep.statblock import StatBlock

# Utility stat -> the module that reads it, or None when nothing does yet.
# Adding a consumer is how a declared stat becomes a wireable one.
UTILITY_STATS: dict[str, str | None] = {
    "gather_yield": "systems/gathering.py",
    "gather_speed": "systems/gathering.py",
    "move_speed": "systems/movement.py",
    "item_rarity": "rewards.py",
    # Declared, deliberately not wireable. `craft_quality` has no crafting
    # system to read it, and `resource_tier_access` belongs to the resource
    # tier/grade work that is not built. Both become wireable by naming their
    # consumer here, once that consumer exists.
    "craft_quality": None,
    "resource_tier_access": None,
}

CONSUMED_UTILITY_STATS = frozenset(
    stat for stat, consumer in UTILITY_STATS.items() if consumer
)

# How a conversion's output joins the pipeline. Same vocabulary as any other
# modifier -- a conversion is not a special kind of bonus, it is an ordinary
# one with an unusual source.
CONVERSION_CLASSES = {
    "flat": "",
    "increased": "_percent",
    "more": "_more",
}

SOURCE_KINDS = ("non_combat_skill", "floor_depth")


class ConversionError(Exception):
    pass


def _magnitude(source: dict, non_combat_levels: dict, floor_number: int) -> float:
    """The scalar a conversion multiplies its coefficient by.

    An unknown skill contributes 0 rather than raising: a character who has
    never touched mineralogy simply has no mineralogy level, and that is the
    normal case at level 1, not an error.
    """
    kind = source["kind"]
    if kind == "non_combat_skill":
        return float(non_combat_levels.get(source["name"], 0) or 0)
    if kind == "floor_depth":
        return float(floor_number or 0)
    raise ConversionError(f"unknown conversion source kind {kind!r}")


def build_block(conversions, non_combat_levels: dict, floor_number: int) -> StatBlock:
    """Turn every configured conversion into one `StatBlock`.

    Pure: takes the player's non-combat levels and current depth as plain
    data and returns pools, so it can be built anywhere and tested without a
    player, a floor, or a registry.
    """
    block = StatBlock()
    for conversion in conversions:
        magnitude = _magnitude(conversion["source"], non_combat_levels, floor_number)
        if not magnitude:
            continue
        suffix = CONVERSION_CLASSES[conversion["class"]]
        block.add(conversion["target"] + suffix, magnitude * float(conversion["per_level"]))
    return block


def resolve(player, floor_number: int, conversions, target: str, base: float) -> float:
    """A utility stat's finished value for this player, right now.

    Equipment and cross-domain contributions share the pools rather than
    applying in sequence, so two sources of `increased gather_yield` sum the
    way two sources of increased anything else do. A conversion is not
    privileged over an affix.
    """
    block = StatBlock()
    block.merge(player.stats.globals)
    block.merge(build_block(conversions, player.skills.non_combat, floor_number))
    return block.resolve(target, base + player.stats.local_totals.get(target, 0.0))


def validate_conversions(conversions, combat_stats) -> None:
    """Reject a conversion config that would break the design constraint.

    `combat_stats` is supplied by the caller (the real ITEM_STATS set) so
    the "no damage scaling" rule is checked against the actual combat
    vocabulary rather than a copy of it that could fall out of date.
    """
    if not isinstance(conversions, list):
        raise ConversionError("cross_domain.json: 'conversions' must be a list")

    for index, conversion in enumerate(conversions):
        where = f"cross_domain.json: conversion {index}"

        for field in ("source", "target", "class", "per_level"):
            if field not in conversion:
                raise ConversionError(f"{where} is missing {field!r}")

        source = conversion["source"]
        if not isinstance(source, dict) or source.get("kind") not in SOURCE_KINDS:
            raise ConversionError(
                f"{where}: 'source.kind' must be one of {list(SOURCE_KINDS)}"
            )
        if source["kind"] == "non_combat_skill" and not source.get("name"):
            raise ConversionError(f"{where}: a non_combat_skill source needs a 'name'")

        target = conversion["target"]
        # Checked before the vocabulary check so that naming a combat stat
        # gets the message explaining *why* it is refused, rather than a
        # generic "unknown target".
        if target in combat_stats:
            raise ConversionError(
                f"{where}: {target!r} is a combat stat. Cross-domain conversions "
                f"may only target utility stats -- letting non-combat progression "
                f"scale combat output makes that progression mandatory. "
                f"Wireable targets: {sorted(CONSUMED_UTILITY_STATS)}"
            )
        if target not in UTILITY_STATS:
            raise ConversionError(
                f"{where}: unknown target {target!r}. "
                f"Wireable targets: {sorted(CONSUMED_UTILITY_STATS)}"
            )
        if UTILITY_STATS[target] is None:
            raise ConversionError(
                f"{where}: {target!r} is declared but nothing reads it yet, so a "
                f"conversion into it would grant nothing. Wire a consumer and "
                f"name it in crossdomain.UTILITY_STATS first."
            )

        if conversion["class"] not in CONVERSION_CLASSES:
            raise ConversionError(
                f"{where}: 'class' must be one of {sorted(CONVERSION_CLASSES)}"
            )

        per_level = conversion["per_level"]
        if not isinstance(per_level, (int, float)) or isinstance(per_level, bool):
            raise ConversionError(f"{where}: 'per_level' must be a number")
