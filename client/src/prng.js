// Deterministic PRNG shared bit-for-bit with server/gep/prng.py.
// See shared/prng_spec.md for the algorithm spec. Do not modify this file
// without mirroring the change in prng.py and re-running the golden test.

export function imul(x, y) {
  return Math.imul(x, y) >>> 0;
}

export function fnv1a32(str) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h = (h ^ str.charCodeAt(i)) >>> 0;
    h = imul(h, 0x01000193);
  }
  return h >>> 0;
}

export class Mulberry32 {
  constructor(seed) {
    this._state = seed >>> 0;
  }

  nextU32() {
    let a = (this._state + 0x6d2b79f5) >>> 0;
    this._state = a;
    let t = a;
    t = imul(t ^ (t >>> 15), t | 1);
    t = (t + imul(t ^ (t >>> 7), t | 61)) >>> 0;
    t = (t ^ (t >>> 14)) >>> 0;
    return t;
  }

  nextFloat() {
    return this.nextU32() / 4294967296;
  }
}

export function seedFromFloor(towerId, floorNumber, globalSeed) {
  return fnv1a32(`${towerId}:${floorNumber}:${globalSeed}`);
}

export function rngForTile(floorSeed, q, r) {
  return new Mulberry32(fnv1a32(`${floorSeed}:${q}:${r}`));
}
