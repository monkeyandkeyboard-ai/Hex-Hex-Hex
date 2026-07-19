// Smooth travel between hexes. The server speaks in whole tiles, one step at
// a time; this turns each step into a timed glide so entities walk instead of
// teleporting.
//
// Interpolation happens in axial (q,r) space, not pixels. hexToPixel is
// affine, so an axial lerp and a pixel lerp draw the same path -- but axial
// survives camera pans and zoom changes, and every position is expressed
// relative to tile centres. Tile centres are the aligned nodes: a leg always
// runs centre-to-centre, so any future alignment fix belongs in hexToPixel
// and this module inherits it for free.
//
// Retargeting mid-step does NOT restart the animation. The current
// interpolated position becomes the new leg's origin, so the entity pivots
// from wherever it actually is and heads for the new tile. It never snaps
// back to the tile it came from or jumps ahead to the one it left.

import { state } from "./state.js";

// Clamp the frame delta so a backgrounded tab (or a breakpoint) doesn't
// deliver one enormous dt that teleports everything on the next frame.
const MAX_FRAME_MS = 250;

// Fraction of a tick a single step takes. 1.0 means a walking entity is
// always in motion: it arrives exactly as the next step arrives.
const STEP_DURATION_TICKS = 1.0;

// Below this axial distance a leg isn't worth animating (float noise, or a
// server correction to the tile we're already standing on).
const EPSILON = 1e-4;

// Facing name -> unit direction, in screen space. Mirrors FACING_BY_DELTA in
// renderer.js and hexgrid.py; see those for the axial deltas.
const FACING_VECTORS = [
  ["down", 0, Math.sqrt(3)],
  ["right-down", 1.5, Math.sqrt(3) / 2],
  ["right-up", 1.5, -Math.sqrt(3) / 2],
  ["up", 0, -Math.sqrt(3)],
  ["left-up", -1.5, -Math.sqrt(3) / 2],
  ["left-down", -1.5, Math.sqrt(3) / 2],
].map(([name, x, y]) => {
  const len = Math.hypot(x, y);
  return { name, x: x / len, y: y / len };
});

// id -> {sq, sr, tq, tr, t, dur, facing, frame}
const legs = new Map();
let lastFrameTime = null;
let frameDelta = 0;
let frameId = 0;

/** Axial delta -> the facing whose direction it most closely matches. */
export function facingFromAxialDelta(dq, dr) {
  // Same linear map as hexToPixel, minus the camera/scale terms: direction is
  // all that matters here, so magnitude cancels out in the normalisation.
  const x = 1.5 * dq;
  const y = Math.sqrt(3) * (dq / 2 + dr);
  const len = Math.hypot(x, y);
  if (len < EPSILON) return null;
  const nx = x / len, ny = y / len;

  let best = null, bestDot = -Infinity;
  for (const dir of FACING_VECTORS) {
    const dot = nx * dir.x + ny * dir.y;
    if (dot > bestDot) { bestDot = dot; best = dir.name; }
  }
  return best;
}

function stepDurationMs() {
  return Math.max(1, (state.tickDuration || 1) * 1000 * STEP_DURATION_TICKS);
}

function positionOf(leg) {
  const k = leg.dur > 0 ? Math.min(1, leg.t / leg.dur) : 1;
  return [
    leg.sq + (leg.tq - leg.sq) * k,
    leg.sr + (leg.tr - leg.sr) * k,
  ];
}

/** Call once at the top of each rendered frame. */
export function beginFrame() {
  const now = performance.now();
  frameDelta = lastFrameTime === null ? 0 : Math.min(MAX_FRAME_MS, now - lastFrameTime);
  lastFrameTime = now;
  frameId++;
}

/**
 * Where to actually draw `id` this frame.
 * @param tile   the entity's authoritative tile from the server
 * @param facing server-reported facing, used while the entity is at rest
 * @returns {{q:number, r:number, facing:string, moving:boolean}}
 */
export function resolve(id, tile, facing) {
  const tq = tile[0], tr = tile[1];
  let leg = legs.get(id);

  if (leg === undefined) {
    // First sight: no glide, just be there. Otherwise every entity would
    // slide in from the origin when a floor snapshot lands.
    leg = { sq: tq, sr: tr, tq, tr, t: 0, dur: 0, facing: facing || "down", frame: frameId };
    legs.set(id, leg);
  } else {
    // Guard against an entity being resolved twice in one frame, which would
    // otherwise advance its clock double-speed.
    if (leg.frame !== frameId) {
      leg.frame = frameId;
      if (leg.t < leg.dur) leg.t = Math.min(leg.dur, leg.t + frameDelta);
    }

    if (tq !== leg.tq || tr !== leg.tr) {
      // Retarget: pivot from the live position, not from the previous tile.
      const [cq, cr] = positionOf(leg);
      const turned = facingFromAxialDelta(tq - cq, tr - cr);
      leg.sq = cq; leg.sr = cr;
      leg.tq = tq; leg.tr = tr;
      leg.t = 0;
      leg.dur = stepDurationMs();
      if (turned !== null) leg.facing = turned;
    }
  }

  const [q, r] = positionOf(leg);
  const moving = leg.t < leg.dur;
  // While walking, the direction of travel is what the sprite should face --
  // after a retarget that differs from the server's last logical step, and
  // the visible motion is the one that must look right. At rest, defer to the
  // server so an idle entity faces where the simulation says it does.
  if (!moving && facing) leg.facing = facing;

  return { q, r, facing: leg.facing, moving };
}

/** Call at the end of each frame: forgets entities that are no longer drawn. */
export function endFrame() {
  for (const [id, leg] of legs) {
    if (leg.frame !== frameId) legs.delete(id);
  }
}

/** Drop all interpolation state (floor change -- nothing should glide). */
export function resetMotion() {
  legs.clear();
  lastFrameTime = null;
}
