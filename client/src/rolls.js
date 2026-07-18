// Mirrors server/gep/rolls.py exactly.

export function weightedChoice(rng, entries) {
  const total = entries.reduce((sum, [, w]) => sum + w, 0);
  const roll = rng.nextFloat() * total;
  let cumulative = 0;
  for (const [entryId, weight] of entries) {
    cumulative += weight;
    if (roll < cumulative) return entryId;
  }
  return entries[entries.length - 1][0];
}
