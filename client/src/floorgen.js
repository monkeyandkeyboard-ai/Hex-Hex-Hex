// Mirrors server/gep/floorgen.py exactly -- same inputs, same layout.

import { ringTiles, tilesInRadius } from "./hexgrid.js";
import { Mulberry32, rngForTile, seedFromFloor } from "./prng.js";
import { weightedChoice } from "./rolls.js";

function tileKey([q, r]) {
  return `${q},${r}`;
}

function floorIndex(rng, n) {
  return Math.min(n - 1, Math.floor(rng.nextFloat() * n));
}

export function generateFloor(towerId, floorNumber, globalSeed, ruleset) {
  const floorSeed = seedFromFloor(towerId, floorNumber, globalSeed);
  const floorRng = new Mulberry32(floorSeed);

  const radius = ruleset.radius;
  const allTiles = tilesInRadius(radius);
  const ringPool = ringTiles(radius, radius);

  const upIdx = floorIndex(floorRng, ringPool.length);
  const upExit = ringPool.splice(upIdx, 1)[0];

  let downExit = null;
  if (floorNumber > 1) {
    const downIdx = floorIndex(floorRng, ringPool.length);
    downExit = ringPool.splice(downIdx, 1)[0];
  }

  const reserved = new Set([tileKey(upExit)]);
  if (downExit) reserved.add(tileKey(downExit));

  const resourceNodes = new Map();
  const resourceChance = ruleset.resource_spawn_chance;
  const resourceWeights = ruleset.resource_weights;
  for (const tile of allTiles) {
    const key = tileKey(tile);
    if (reserved.has(key)) continue;
    const [q, r] = tile;
    const tileRng = rngForTile(floorSeed, q, r);
    if (tileRng.nextFloat() < resourceChance) {
      resourceNodes.set(key, weightedChoice(tileRng, resourceWeights));
    }
  }

  const available = allTiles.filter((t) => {
    const key = tileKey(t);
    return !reserved.has(key) && !resourceNodes.has(key);
  });
  const spawnPool = available.slice();
  const monsterSpawns = [];
  const spawnCount = Math.min(ruleset.monster_spawn_count, spawnPool.length);
  for (let i = 0; i < spawnCount; i++) {
    const idx = floorIndex(floorRng, spawnPool.length);
    const tile = spawnPool.splice(idx, 1)[0];
    const templateId = weightedChoice(floorRng, ruleset.monster_weights);
    monsterSpawns.push({ tile, template_id: templateId });
  }

  return {
    tower_id: towerId,
    floor_number: floorNumber,
    radius,
    tiles: allTiles,
    up_exit: upExit,
    down_exit: downExit,
    resource_nodes: resourceNodes,
    monster_spawns: monsterSpawns,
  };
}
