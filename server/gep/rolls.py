"""One generic weighted-roll primitive, reused for resource types, monster
templates, loot tables, visual parts -- anywhere the compendium calls for a
"weighted roll" (§12, §13.3, §13.4). Never reimplement this per system.
"""
from gep.prng import Mulberry32


def weighted_choice(rng: Mulberry32, entries: list[tuple[str, float]]) -> str:
    """entries: [(id, weight), ...], weights > 0. Deterministic given rng
    state. Mirrors client/src/rolls.js exactly (same draw: one next_float(),
    same cumulative-weight scan order as given).
    """
    total = sum(w for _, w in entries)
    roll = rng.next_float() * total
    cumulative = 0.0
    for entry_id, weight in entries:
        cumulative += weight
        if roll < cumulative:
            return entry_id
    return entries[-1][0]
