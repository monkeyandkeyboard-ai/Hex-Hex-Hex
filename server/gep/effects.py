"""Timed active effects: the substrate the ability system composes from.

An *effect* is a payload that sits on an entity. Two shapes:

  immediate -- applied once, right now: `damage`, `heal`. These never live in
               `active_effects`; a caller applies them and they are gone.
  timed     -- registered onto `entity.active_effects` and processed every tick
               by systems/effects.py until they expire: `dot`, `hot`, `buff`,
               `debuff`, `shield`, `stun`, `root`, `slow`.

This module is the pure model: the `Effect` record plus the read-only queries
combat and movement consult (stat bonuses, control gates, shield absorption).
It imports nothing from `entities` or `systems`, so both may import it without
a cycle -- `entities.combat_stat` folds in `stat_bonus`, the systems drive the
per-tick sweep, and combat's damage application asks it to spend shields.

Kept deliberately separate from the *stats* pipeline (statblock.py): a buff is
an additive term read at `combat_stat` time, never a modifier folded into the
equipment aggregation. When the stats rework settles, this layer does not move.
"""
from dataclasses import dataclass, field

# Applied once and discarded -- never stored on an entity. `cleanse` strips
# debuffs from its targets on application (see systems/abilities.py).
IMMEDIATE_KINDS = frozenset({"damage", "heal", "cleanse"})
# Periodic damage/heal, ticked by the sweep.
PERIODIC_KINDS = frozenset({"dot", "hot"})
# Additive modifiers to a named combat stat, read at combat_stat time.
STAT_KINDS = frozenset({"buff", "debuff"})
# Gate what an entity may *do* while active. Each is read by exactly one
# system: stun by all action handlers, root/haste/slow by movement, silence by
# the ability handler, disarm by weapon attacks, taunt by monster targeting,
# invulnerable by damage application.
CONTROL_KINDS = frozenset({"stun", "root", "silence", "disarm", "invulnerable", "taunt"})
# Change movement cadence.
MOVE_KINDS = frozenset({"slow", "haste"})
# Change how much incoming damage lands: a shield absorbs a pool, fortify/
# vulnerability scale it. All consulted by entity.take_damage.
MITIGATION_KINDS = frozenset({"shield", "fortify", "vulnerability"})
TIMED_KINDS = PERIODIC_KINDS | STAT_KINDS | CONTROL_KINDS | MOVE_KINDS | MITIGATION_KINDS
# The debuffs a `cleanse` removes -- everything unwanted, never a buff/shield.
CLEANSABLE_KINDS = frozenset({"dot", "debuff", "slow", "stun", "root",
                              "silence", "disarm", "vulnerability"})
EFFECT_KINDS = IMMEDIATE_KINDS | TIMED_KINDS


@dataclass
class Effect:
    """One live timed effect on an entity.

    `effect_id` is the stacking identity: re-applying the same id from the same
    kind refreshes the existing effect rather than stacking a second copy, so a
    poison re-hit resets its clock instead of doubling its damage. `source_id`
    is who to credit for periodic damage XP and loot; it may name an entity that
    has since left the floor, in which case the credit is simply skipped.
    """
    effect_id: str
    kind: str
    expires_tick: int
    source_id: str | None = None
    # dot / hot: precomputed amount per periodic application and its cadence.
    # Snapshotted at apply time so a lingering poison does not re-scale off the
    # caster's stats every tick (and needs no live caster reference to tick).
    tick_amount: float = 0.0
    interval: int = 1
    next_tick: int = 0
    damage_type: str = "physical"
    # The stats a dot should train on the source, mirroring how a swing credits
    # its driving stat. {stat: coeff}; empty for a monster-sourced dot.
    train_power: dict = field(default_factory=dict)
    # buff / debuff: signed at read time by kind. `stat` is a combat skill.
    magnitude: float = 0.0
    stat: str | None = None
    # slow: fraction in [0, 1) taken off movement speed/cadence.
    slow_fraction: float = 0.0
    # haste: fraction in [0, 1) added to movement speed/cadence.
    haste_fraction: float = 0.0
    # shield: remaining absorb pool.
    absorb_remaining: float = 0.0


def stat_bonus(active_effects: list, stat: str) -> float:
    """Net additive modifier active timed effects contribute to `stat`.

    Buffs add, debuffs subtract. Summed (not max'd) so two independent buffs
    both count; a design that wants diminishing returns tunes the magnitudes,
    not this. Returns 0.0 for an entity with no relevant effects -- the common
    case, kept cheap since `combat_stat` runs several times per swing.
    """
    if not active_effects:
        return 0.0
    total = 0.0
    for e in active_effects:
        if e.stat != stat:
            continue
        if e.kind == "buff":
            total += e.magnitude
        elif e.kind == "debuff":
            total -= e.magnitude
    return total


def is_stunned(active_effects: list) -> bool:
    """A stunned entity may take no action (attack, cast, move)."""
    return any(e.kind == "stun" for e in active_effects)


def is_rooted(active_effects: list) -> bool:
    """A rooted entity may act but may not move."""
    return any(e.kind == "root" for e in active_effects)


def is_silenced(active_effects: list) -> bool:
    """A silenced entity may move and attack but may not cast abilities."""
    return any(e.kind == "silence" for e in active_effects)


def is_disarmed(active_effects: list) -> bool:
    """A disarmed entity may move and cast but may not make weapon attacks."""
    return any(e.kind == "disarm" for e in active_effects)


def is_invulnerable(active_effects: list) -> bool:
    """An invulnerable entity takes no damage from any source."""
    return any(e.kind == "invulnerable" for e in active_effects)


def taunt_source(active_effects: list) -> str | None:
    """The id of whoever last taunted this entity, or None. Forces a monster's
    target while active (systems/monster_ai.py) -- the tank's pull."""
    src = None
    for e in active_effects:
        if e.kind == "taunt":
            src = e.source_id
    return src


def pace_factor(active_effects: list) -> float:
    """Multiplier on movement delay/interval from slow and haste combined.

    >1 slows (longer between steps), <1 hastens. Slow and haste each take the
    strongest of their kind (max, not sum, so stacks cannot invert speed), then
    net against each other. A caller multiplies its base cadence by this and
    clamps to its own floor (players cannot sub-tick; monsters can shorten an
    interval). 1.0 when neither is present -- the common, cheap case.
    """
    slow = max((e.slow_fraction for e in active_effects if e.kind == "slow"), default=0.0)
    haste = max((e.haste_fraction for e in active_effects if e.kind == "haste"), default=0.0)
    net = slow - haste
    if net == 0:
        return 1.0
    if net > 0:
        return 1.0 / (1.0 - min(net, 0.95))
    return max(0.1, 1.0 + net)   # net haste shortens, floored so it stays finite


def damage_taken_multiplier(active_effects: list) -> float:
    """Combined scalar on incoming damage from timed fortify (reduces) and
    vulnerability (amplifies). Each source multiplies independently, so a 20%
    fortify and a 50% vulnerability net to 0.8 * 1.5 = 1.2x."""
    mult = 1.0
    for e in active_effects:
        if e.kind == "fortify":
            mult *= max(0.0, 1.0 - e.magnitude)
        elif e.kind == "vulnerability":
            mult *= 1.0 + e.magnitude
    return mult


def cleanse(active_effects: list) -> list[str]:
    """Remove every cleansable debuff in place; return the ids removed (for the
    event log). Buffs, shields and hots are left untouched."""
    removed = [e.effect_id for e in active_effects if e.kind in CLEANSABLE_KINDS]
    active_effects[:] = [e for e in active_effects if e.kind not in CLEANSABLE_KINDS]
    return removed


def consume_absorb(active_effects: list, amount: float) -> float:
    """Spend shield pools against `amount` of incoming damage, in place.

    Returns how much was absorbed (0..amount). Depleted shields are left at
    absorb_remaining 0 for the sweep to expire and announce -- this function
    stays a pure arithmetic spend so it is safe to call from the hot damage
    path with no event plumbing.
    """
    if amount <= 0:
        return 0.0
    absorbed = 0.0
    for e in active_effects:
        if e.kind != "shield" or e.absorb_remaining <= 0:
            continue
        take = min(e.absorb_remaining, amount - absorbed)
        e.absorb_remaining -= take
        absorbed += take
        if absorbed >= amount:
            break
    return absorbed


def apply_timed(active_effects: list, effect: Effect) -> None:
    """Add `effect`, refreshing any existing effect of the same id+kind rather
    than stacking a duplicate (see Effect.effect_id). The refreshed effect
    takes the new one's values outright -- a stronger re-application wins, a
    weaker one still resets the clock, which is the conventional MUD behaviour.
    """
    for i, e in enumerate(active_effects):
        if e.effect_id == effect.effect_id and e.kind == effect.kind:
            active_effects[i] = effect
            return
    active_effects.append(effect)


def effects_payload(active_effects: list, tick: int) -> list[dict]:
    """Client-facing view of an entity's active effects for the wire. Remaining
    duration is derived here so the client renders a countdown without knowing
    the server's tick number."""
    return [
        {
            "effect_id": e.effect_id,
            "kind": e.kind,
            "stat": e.stat,
            "remaining_ticks": max(0, e.expires_tick - tick),
            "absorb_remaining": e.absorb_remaining if e.kind == "shield" else None,
        }
        for e in active_effects
    ]
