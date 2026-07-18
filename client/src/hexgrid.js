// Axial hex-coordinate utilities, mirrors server/gep/hexgrid.py exactly.

export function tileCount(radius) {
  return 3 * radius * radius + 3 * radius + 1;
}

export function tilesInRadius(radius) {
  const tiles = [];
  for (let q = -radius; q <= radius; q++) {
    const rLo = Math.max(-radius, -q - radius);
    const rHi = Math.min(radius, -q + radius);
    for (let r = rLo; r <= rHi; r++) {
      tiles.push([q, r]);
    }
  }
  return tiles;
}

export function hexDistance(a, b) {
  const [aq, ar] = a;
  const [bq, br] = b;
  const ax = aq, az = ar, ay = -ax - az;
  const bx = bq, bz = br, by = -bx - bz;
  return Math.max(Math.abs(ax - bx), Math.abs(ay - by), Math.abs(az - bz));
}

export function ringTiles(radius, ringRadius) {
  return tilesInRadius(radius).filter((t) => hexDistance([0, 0], t) === ringRadius);
}
