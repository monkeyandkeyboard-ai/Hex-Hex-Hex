// Canvas2D hex renderer. Reads from state, draws, never modifies state.
// Hex layout: flat-top pointy-top axial. Size = pixel radius of a tile.

import { state } from "./state.js";
import { beginFrame, endFrame, resolve as resolveMotion } from "./motion.js";

const COLORS = {
  bg:           "#0d0d0f",
  tile:         "#1a1a1f",
  tileBorder:   "#252530",
  tileHover:    "#252535",
  upExit:       "#1a3a1a",
  downExit:     "#1a1a3a",
  road:         "#5a4a30",
  // Planking on a carved crossing. Warmer and lighter than the road so a
  // bridge stands out against both dark water and grey rock, and reads as
  // built rather than as another shade of ground.
  crossing:     "#a07a45",
  resource:     "#3a2a10",
  resourceDot:  "#c8901a",
  monster:      "#3a1010",
  monsterDot:   "#e05050",
  selfPlayer:   "#103a10",
  selfDot:      "#50e050",
  otherPlayer:  "#10103a",
  otherDot:     "#5050e0",
};

// Reserved tile types (server gep/tiles.py). These are placeholders and are
// meant to look like placeholders: a stark two-tone checkerboard reads as
// "deliberately unfinished" at a glance and is impossible to miss against the
// muted procedural terrain, which is exactly what stairs need until real art
// exists. When art lands, replace the pattern -- the lookup, the wire field,
// and the draw pass all stay.
const TILE_TYPE_STYLE = {
  tile_stairs_up:   { base: "#0f2a0f", light: "#7dffa0", dark: "#0a1f0a",
                      border: "#9dffb8", glyph: "▲", glyphColor: "#eaffee" },
  tile_stairs_down: { base: "#0f0f2a", light: "#8fb4ff", dark: "#0a0a1f",
                      border: "#b0c8ff", glyph: "▼", glyphColor: "#eef3ff" },
};
// Checker square size in pattern space, at the base tile size the terrain
// textures were authored against. Scaled with the tiles like the others.
const CHECKER_SIZE = 7;

let canvas, ctx;
let tileSize = 28;
let hoveredTile = null;

export function initRenderer(canvasEl) {
  canvas = canvasEl;
  ctx = canvas.getContext("2d");
  syncCanvasSize();
  // ResizeObserver, not just window.onresize: the canvas box also changes
  // from layout (sidebar width, pane reflow, an embedding iframe resizing)
  // with no window resize event. When the backing store drifts from the CSS
  // box the browser stretches the drawing while pointer coords stay in CSS
  // pixels, so the hover highlight slides off the cursor.
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(syncCanvasSize).observe(canvas);
  } else {
    window.addEventListener("resize", syncCanvasSize);
  }
  canvas.addEventListener("mousemove", onMouseMove);
  canvas.addEventListener("mouseleave", () => { hoveredTile = null; });
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("mousedown", onMouseDown);
  // Chrome/Windows opens its autoscroll widget on middle mousedown. The
  // mousedown preventDefault below stops it, but the browser also fires
  // `auxclick` on release, and a stray one over the canvas is a click the
  // rest of the app has no reason to see.
  canvas.addEventListener("auxclick", (e) => {
    if (e.button === MIDDLE_BUTTON) e.preventDefault();
  });
  // Middle-drag is a two-handed gesture: the button goes down on the canvas
  // but the cursor routinely leaves it mid-drag (the map is panned toward an
  // edge, or the sidebar is in the way). Tracking on window rather than the
  // canvas means the pan follows the cursor off the element and, more
  // importantly, that releasing the button anywhere ends it -- bound to the
  // canvas, a release outside it is never delivered and the map stays stuck
  // to the cursor with no button held.
  window.addEventListener("mousemove", onDragMove);
  window.addEventListener("mouseup", onMouseUp);
  // A drag interrupted by an alt-tab or a devtools break never gets its
  // mouseup either, and would resume panning on the next unrelated move.
  window.addEventListener("blur", endPan);
}

const ZOOM_MIN = 0.3, ZOOM_MAX = 5.0, ZOOM_STEP = 1.15;

function onWheel(e) {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();

  const oldZoom = state.zoom;
  const zoomingIn = e.deltaY < 0;
  const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN,
    zoomingIn ? oldZoom * ZOOM_STEP : oldZoom / ZOOM_STEP));
  if (newZoom === oldZoom) return;

  // The anchor is the one screen point the zoom holds still. Which point that
  // should be differs by direction, because the two directions are asking for
  // different things:
  //
  //   in  -> the cursor. Zooming in is aiming at something specific, and the
  //          cursor is the player pointing at it.
  //   out -> the viewport centre. Zooming out is asking for context around
  //          where you already are. Anchored on the cursor it instead pushes
  //          the middle of the screen toward wherever the pointer happened to
  //          be resting, so the thing you were looking at slides off toward a
  //          corner -- worst exactly when the pointer is near an edge, which
  //          is where it sits after clicking a move target.
  //
  // The cost of splitting them is that zoom stops being its own inverse: a
  // wheel-in then wheel-out does not land back on the original framing unless
  // the cursor was already centred. That is a deliberate trade -- each
  // direction reads correctly on its own, which is what the player actually
  // experiences, and neither one is ever surprising in isolation.
  const anchorX = zoomingIn ? e.clientX - rect.left : rect.width / 2;
  const anchorY = zoomingIn ? e.clientY - rect.top : rect.height / 2;

  // Keep the world point under the anchor fixed: world scale is proportional
  // to zoom, so scale the anchor->camera offset by the ratio.
  const k = newZoom / oldZoom;
  state.cameraX = anchorX - (anchorX - state.cameraX) * k;
  state.cameraY = anchorY - (anchorY - state.cameraY) * k;
  state.zoom = newZoom;
}

// --- Middle-drag panning --------------------------------------------------
// The camera offset is in screen pixels, so a pan is just the cursor delta
// added to it -- no zoom or hex maths involved, at any zoom level.

const MIDDLE_BUTTON = 1;
let panning = false;
let panLastX = 0, panLastY = 0;

function onMouseDown(e) {
  if (e.button !== MIDDLE_BUTTON) return;
  // Suppresses the autoscroll widget, which would otherwise hijack the drag
  // and scroll the page under the canvas.
  e.preventDefault();
  panning = true;
  panLastX = e.clientX;
  panLastY = e.clientY;
  canvas.style.cursor = "grabbing";
}

function onDragMove(e) {
  if (!panning) return;
  // Deltas against the previous event rather than against the press origin:
  // the camera is mutated in place (the wheel handler writes it too), so an
  // absolute offset from a remembered start would fight anything else that
  // moves the camera mid-drag.
  state.cameraX += e.clientX - panLastX;
  state.cameraY += e.clientY - panLastY;
  panLastX = e.clientX;
  panLastY = e.clientY;
}

function onMouseUp(e) {
  if (e.button === MIDDLE_BUTTON) endPan();
}

function endPan() {
  if (!panning) return;
  panning = false;
  canvas.style.cursor = "";
}

// Point the backing store at the current CSS box. Cheap to call every frame:
// it only touches the canvas when the size actually drifted, and resizing a
// canvas clears it. setTransform (not scale) because assigning width/height
// resets the transform, and scale() would compound on the calls where the
// size was unchanged.
function syncCanvasSize() {
  if (!canvas) return;
  const dpr = devicePixelRatio;
  const w = Math.round(canvas.clientWidth * dpr);
  const h = Math.round(canvas.clientHeight * dpr);
  if (w === 0 || h === 0) return;  // laid out to zero (hidden tab) -- skip
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

// Axial -> pixel (flat-top)
function hexToPixel(q, r) {
  const s = tileSize;
  const x = s * (3/2 * q) + state.cameraX;
  const y = s * (Math.sqrt(3)/2 * q + Math.sqrt(3) * r) + state.cameraY;
  return [x, y];
}

// Pixel -> axial (flat-top, fractional)
function pixelToHex(px, py) {
  const s = tileSize;
  const x = px - state.cameraX;
  const y = py - state.cameraY;
  const q = (2/3 * x) / s;
  const r = (-1/3 * x + Math.sqrt(3)/3 * y) / s;
  return cubeRound(q, r);
}

function cubeRound(fq, fr) {
  const fs = -fq - fr;
  let q = Math.round(fq), r = Math.round(fr), s = Math.round(fs);
  const dq = Math.abs(q - fq), dr = Math.abs(r - fr), ds = Math.abs(s - fs);
  if (dq > dr && dq > ds) q = -r - s;
  else if (dr > ds) r = -q - s;
  return [q, r];
}

// Unit hex corner offsets, computed once rather than six sin/cos per hex.
const HEX_COS = [], HEX_SIN = [];
for (let i = 0; i < 6; i++) {
  const angle = Math.PI / 180 * (60 * i);
  HEX_COS.push(Math.cos(angle));
  HEX_SIN.push(Math.sin(angle));
}

/** Add one hex outline to the current path (no beginPath / no paint). */
function addHex(cx, cy, size) {
  ctx.moveTo(cx + size * HEX_COS[0], cy + size * HEX_SIN[0]);
  for (let i = 1; i < 6; i++) {
    ctx.lineTo(cx + size * HEX_COS[i], cy + size * HEX_SIN[i]);
  }
  ctx.closePath();
}

function hexPath(cx, cy, size) {
  ctx.beginPath();
  addHex(cx, cy, size);
}

function drawTileAt(cx, cy, fill, stroke) {
  hexPath(cx, cy, tileSize - 1);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = BORDER_WIDTH;
  ctx.stroke();
}

function drawTile(q, r, fill, stroke) {
  const [cx, cy] = hexToPixel(q, r);
  drawTileAt(cx, cy, fill, stroke);
}

// --- Per-floor tile geometry ---------------------------------------------
// The tile loop runs over every tile on the floor each frame, so anything
// derived from a tile's fixed (q, r) is computed once per floor here instead
// of per frame: unit pixel offsets (multiply by tileSize and add the camera
// to get a position, with no array allocation), the index of the tile above
// for slope shading, and a road flag. Previously each of these meant an
// allocation or a template-string Map key 12.5k times a frame, which cost
// far more than the canvas drawing itself.

// Axial neighbour offsets, ordered clockwise from straight up-screen. The
// order is load-bearing, not cosmetic: the occlusion pass below weights the
// three up-screen directions (indices 0, 1, 5) more heavily than the three
// below, which is what turns a symmetric height difference into a light
// direction. Renumber these and the terrain lights from the wrong side.
const AXIAL_DIRS = [
  [0, -1],   // 0: up
  [1, -1],   // 1: upper-right
  [1, 0],    // 2: lower-right
  [0, 1],    // 3: down
  [-1, 1],   // 4: lower-left
  [-1, 0],   // 5: upper-left
];
// Per-direction weight for the occlusion term. Up-screen neighbours cast onto
// this tile (a wall to the north shadows the ground at its foot); down-screen
// neighbours are behind the light and contribute little.
const OCCLUSION_WEIGHTS = [1.0, 0.75, 0.25, 0.15, 0.25, 0.75];

let geometry = null;  // { order, ux, uy, up, road, crossing, nbr }

function tileGeometry() {
  const order = state.tileOrder;
  if (geometry !== null && geometry.order === order) return geometry;

  const n = order.length;
  const ux = new Float32Array(n);
  const uy = new Float32Array(n);
  const up = new Int32Array(n);
  const road = new Uint8Array(n);
  const crossing = new Uint8Array(n);
  // All six axial neighbours, flattened: neighbours of tile i live at
  // [i*6 .. i*6+5], -1 where the neighbour is off the floor edge. One flat
  // Int32Array rather than an array of arrays because the shading pass reads
  // it 12.5k * 6 times a frame, and n small arrays would mean n allocations
  // and a pointer chase per sample.
  const nbr = new Int32Array(n * 6);
  const SQRT3 = Math.sqrt(3);

  for (let i = 0; i < n; i++) {
    const q = order[i][0], r = order[i][1];
    ux[i] = 1.5 * q;
    uy[i] = SQRT3 * (q / 2 + r);
    const iUp = state.tileIndex.get(`${q},${r - 1}`);
    up[i] = iUp === undefined ? -1 : iUp;
    for (let d = 0; d < 6; d++) {
      const j = state.tileIndex.get(`${q + AXIAL_DIRS[d][0]},${r + AXIAL_DIRS[d][1]}`);
      nbr[i * 6 + d] = j === undefined ? -1 : j;
    }
    road[i] = state.roads.has(`${q},${r}`) ? 1 : 0;
    crossing[i] = state.crossings.has(`${q},${r}`) ? 1 : 0;
  }

  geometry = { order, ux, uy, up, road, crossing, nbr };
  return geometry;
}

/** Drop cached geometry (new floor, or roads changed). */
export function resetGeometry() {
  geometry = null;
}

// --- Procedural HSL stack -------------------------------------------------
// Layer 1 base biome hue/sat/lightness, Layer 2 roughness micro-texture,
// Layer 3 directional elevation shading, Layer 4 ambient occlusion from all
// six neighbours. Colour strings are cached by (biome, quantised lightness)
// since building 12k hsl() strings per frame would dominate the frame budget.
//
// Every layer resolves to one number -- a lightness -- before any drawing
// happens, which is the property that keeps the whole stack inside the
// batched fill path. Adding occlusion widens the spread of lightnesses but
// not the *count* of them, because celStep quantises whatever the layers sum
// to; the bucket count is bounded by the cel steps, not by the layers.

const BORDER_WIDTH = 1;
// Below this tile size, a screenful is thousands of hexes and per-hex
// stroking dominates the frame; above it, tile counts are low enough that
// per-hex painting is cheap and the 1px border actually reads. Measured:
// at tileSize 8 per-hex costs ~25ms/frame vs ~10ms batched.
const BATCH_BELOW_TILE_SIZE = 14;
const ROUGHNESS_AMOUNT = 7;   // lightness swing from micro-texture
const ELEVATION_AMOUNT = 10;  // lightness swing from absolute height
// Directional shading strength. Cut from 30 when occlusion arrived: slope is
// a one-neighbour approximation of the same height difference occlusion now
// samples properly across six, so at their old strengths the two stacked and
// drove the ground at the foot of any cliff to solid black. Slope earns its
// remaining weight by being signed -- it still lifts up-facing slopes into
// highlight, which the occlusion term deliberately never does.
const SLOPE_AMOUNT = 13;
// Ambient occlusion: how much a tile darkens when its neighbours stand above
// it. Applied on top of SLOPE_AMOUNT rather than replacing it -- slope gives
// the surface a lit face and a dark face, occlusion sinks whole hollows into
// shadow and leaves ridges proud. Together they read as relief; either alone
// reads as a gradient.
const OCCLUSION_AMOUNT = 17;
// Occlusion only, no brightening: a tile lower than everything around it is
// in a pit and genuinely receives less light, but a tile *higher* than its
// neighbours is not receiving extra light -- it is just unshadowed. Letting
// the term go positive blows ridge tops out to a flat bright cap and loses
// the surface detail the textures are drawing there.
// Capped below the theoretical maximum (a tile ringed by full-height cliffs
// would score ~3.1). Left uncapped, the 0.35 height boost on a wall biome is
// on its own enough to drive the neighbouring ground to the lightness floor,
// and a black rim reads as a hole punched in the map rather than as shadow --
// it also swallows the surface texture the tile is drawing underneath.
const OCCLUSION_CLAMP = 0.8;

// Stepped cap drawn on impassable tiles, outermost first. Scales are fractions
// of the hex radius; the fills are deliberately weak because they stack -- the
// centre of a barrier tile receives both, so the visible step count is three
// (plate, mid, cap) from two draws.
const CONTOUR_TIERS = [
  { scale: 0.70, fill: "rgba(255,255,255,0.055)" },
  { scale: 0.40, fill: "rgba(255,255,255,0.075)" },
];

// Cel shading: lightness is snapped to fixed steps so the three layers above
// resolve into flat bands of colour instead of a smooth gradient. This is the
// whole stylised look in one line -- and it pays for itself, because snapping
// collapses ~12k distinct per-tile lightnesses down to a handful of colours,
// which is what makes the batched fill path viable at every zoom level rather
// than only when zoomed out.
const CEL_STEP = 6;
// Borders are the tile's own colour darkened, not a fixed grey, so separation
// reads consistently against every biome (compendium §19: colour comes from
// config, the client only derives from it).
const BORDER_DARKEN = 14;

function celStep(lightness) {
  return Math.round(lightness / CEL_STEP) * CEL_STEP;
}

const colorCache = new Map();

function tileColor(biomeIdx, lightness) {
  const li = Math.max(2, Math.min(70, Math.round(lightness)));
  const key = biomeIdx * 128 + li;
  let c = colorCache.get(key);
  if (c === undefined) {
    const biome = state.biomes[state.biomeLegend[biomeIdx]];
    const hsl = (biome && biome.hsl) || { h: 0, s: 0, l: 20 };
    c = `hsl(${hsl.h}, ${hsl.s}%, ${li}%)`;
    colorCache.set(key, c);
  }
  return c;
}

// --- Procedural tile textures --------------------------------------------
// Zero art assets: each biome's surface texture is generated into an offscreen
// canvas once and reused as a CanvasPattern. Generating per frame would be
// hopeless, and shipping PNGs for this is exactly what we're avoiding.
//
// The texture carries only alpha -- light and dark speckles over transparency
// -- so it modulates whatever colour the cel-shaded fill already laid down.
// That keeps one texture per biome valid across every lightness band instead
// of needing one per (biome, band).
//
// Style and strength come from the biome's `texture` block in
// server/config/biomes/*.json. A biome without one renders flat, which is a
// fine look and keeps the field genuinely optional.

// Large enough that the repeat isn't legible as a grid across a screenful of
// hexes. At 64 the tiling read as a visible lattice of identical blobs.
const PATTERN_SIZE = 96;
// Below this tile size the texture is skipped entirely -- see the gate in
// render(). Slightly under the border threshold so texture survives one zoom
// step past where borders drop out.
const TEXTURE_MIN_TILE_SIZE = 12;
const patternCache = new Map();  // biome id -> CanvasPattern | null

// A tiny local PRNG so a biome's speckle layout is identical every session
// (a texture that reshuffled on reload would shimmer between visits). This is
// cosmetic only and deliberately NOT the game's Mulberry32 -- nothing here
// feeds simulation, so it is not bound by the determinism contract in
// shared/prng_spec.md.
function textureRng(seedStr) {
  let h = 2166136261;
  for (let i = 0; i < seedStr.length; i++) {
    h ^= seedStr.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return function () {
    h += 0x6D2B79F5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function buildPattern(biomeId, tex) {
  const off = document.createElement("canvas");
  off.width = off.height = PATTERN_SIZE;
  const g = off.getContext("2d");

  const rand = textureRng(biomeId);
  const density = tex.density ?? 0.4;
  const contrast = tex.contrast ?? 0.15;
  const grain = tex.grain ?? 2.0;
  const style = tex.style || "speckle";

  // Two tones per style -- one lighter than the fill, one darker -- so the
  // texture reads as surface relief rather than as dirt.
  const light = `rgba(255,255,255,${contrast})`;
  const dark = `rgba(0,0,0,${contrast * 1.3})`;

  if (style === "hatch") {
    // Diagonal strokes. Wraps because every line is drawn twice, offset by
    // the tile size, so the pattern tiles seamlessly across the edge.
    g.lineWidth = grain * 0.5;
    const step = Math.max(3, 14 - density * 12);
    for (let d = -PATTERN_SIZE; d < PATTERN_SIZE * 2; d += step) {
      g.strokeStyle = rand() < 0.5 ? light : dark;
      g.beginPath();
      g.moveTo(d, 0);
      g.lineTo(d + PATTERN_SIZE, PATTERN_SIZE);
      g.stroke();
    }
  } else if (style === "crag") {
    // Angular chevrons -- reads as faceted rock or a wall of tangled growth,
    // depending on the hue underneath. This is the shared visual language for
    // impassable terrain: the biome's `passable` flag is what actually stops
    // movement, but a barrier the player cannot distinguish from open ground
    // is a barrier they can only find by walking into it. Every impassable
    // land biome uses this style so the cue is one rule to learn rather than
    // one per biome, and any future barrier gets it by adding the same block.
    //
    // Deliberately *not* driven off `passable` in code: the renderer reads
    // biome appearance from config like every other visual property, so a
    // designer can retune or restyle a barrier without touching this file.
    const count = Math.round(density * 34);
    g.lineWidth = grain * 0.8;
    g.lineCap = "square";
    for (let i = 0; i < count; i++) {
      const x = rand() * PATTERN_SIZE;
      const y = rand() * PATTERN_SIZE;
      const w = grain * (2.2 + rand() * 2.6);   // half-width of the chevron
      const h = grain * (1.6 + rand() * 2.2);   // how sharply it peaks
      const flip = rand() < 0.35 ? -1 : 1;      // a few point downward
      const tone = rand() < 0.5 ? light : dark;
      // Nine wrap offsets, same reason as `cell`: a chevron crossing the
      // pattern edge has to continue on the far side or it tiles as a seam.
      for (let ox = -1; ox <= 1; ox++) {
        for (let oy = -1; oy <= 1; oy++) {
          const px = x + ox * PATTERN_SIZE;
          const py = y + oy * PATTERN_SIZE;
          g.strokeStyle = tone;
          g.beginPath();
          g.moveTo(px - w, py);
          g.lineTo(px, py - h * flip);
          g.lineTo(px + w, py);
          g.stroke();
        }
      }
    }
  } else if (style === "canopy") {
    // Overlapping domed clusters -- reads as a tree line packed too tight to
    // walk through. Each clump is a filled arc with a lighter crown on top, so
    // the layer has a direction (lit from above) instead of reading as flat
    // polka dots the way plain `cell` does.
    const count = Math.round(density * 30);
    for (let i = 0; i < count; i++) {
      const x = rand() * PATTERN_SIZE;
      const y = rand() * PATTERN_SIZE;
      const rad = grain * (1.3 + rand() * 1.5);
      for (let ox = -1; ox <= 1; ox++) {
        for (let oy = -1; oy <= 1; oy++) {
          const px = x + ox * PATTERN_SIZE;
          const py = y + oy * PATTERN_SIZE;
          g.beginPath();
          g.arc(px, py, rad, 0, Math.PI * 2);
          g.fillStyle = dark;
          g.fill();
          // Crown: a smaller arc offset up-left, the lit face of the clump.
          g.beginPath();
          g.arc(px - rad * 0.3, py - rad * 0.35, rad * 0.55, 0, Math.PI * 2);
          g.fillStyle = light;
          g.fill();
        }
      }
    }
  } else if (style === "wave") {
    // Horizontal ripple lines. Full-width sine strokes rather than scattered
    // marks: water's read comes from the lines being continuous and level,
    // which is also what makes it look like a surface rather than terrain.
    // Wraps horizontally because each stroke spans the whole tile width, and
    // vertically because rows are spaced evenly into PATTERN_SIZE.
    const rows = Math.max(3, Math.round(density * 12));
    const step = PATTERN_SIZE / rows;
    g.lineWidth = grain * 0.5;
    g.lineCap = "round";
    for (let rw = 0; rw < rows; rw++) {
      const baseY = rw * step + step * 0.5;
      const amp = grain * (0.5 + rand() * 0.7);
      const phase = rand() * Math.PI * 2;
      // Integer cycle count so the sine closes on itself at the pattern edge.
      const cycles = 1 + Math.floor(rand() * 2);
      g.strokeStyle = rand() < 0.5 ? light : dark;
      g.beginPath();
      for (let x = 0; x <= PATTERN_SIZE; x += 2) {
        const y = baseY + Math.sin(phase + (x / PATTERN_SIZE) * Math.PI * 2 * cycles) * amp;
        if (x === 0) g.moveTo(x, y); else g.lineTo(x, y);
      }
      g.stroke();
    }
  } else if (style === "cell") {
    // Soft blobs -- reads as fungal/organic clumping at tile scale.
    const count = Math.round(density * 26);
    for (let i = 0; i < count; i++) {
      const x = rand() * PATTERN_SIZE;
      const y = rand() * PATTERN_SIZE;
      const rad = grain * (0.6 + rand() * 1.4);
      // Draw each blob at all 9 wrap offsets so blobs crossing an edge
      // continue on the far side instead of being clipped into a seam.
      for (let ox = -1; ox <= 1; ox++) {
        for (let oy = -1; oy <= 1; oy++) {
          g.beginPath();
          g.arc(x + ox * PATTERN_SIZE, y + oy * PATTERN_SIZE, rad, 0, Math.PI * 2);
          g.fillStyle = rand() < 0.45 ? light : dark;
          g.fill();
        }
      }
    }
  } else {
    // speckle: scattered grains, the default stone/gravel read.
    const count = Math.round(density * 200);
    for (let i = 0; i < count; i++) {
      const x = rand() * PATTERN_SIZE;
      const y = rand() * PATTERN_SIZE;
      const s = grain * (0.5 + rand());
      g.fillStyle = rand() < 0.5 ? light : dark;
      g.fillRect(x, y, s, s);
      // Wrap grains that overhang an edge.
      if (x + s > PATTERN_SIZE) g.fillRect(x - PATTERN_SIZE, y, s, s);
      if (y + s > PATTERN_SIZE) g.fillRect(x, y - PATTERN_SIZE, s, s);
    }
  }

  return ctx.createPattern(off, "repeat");
}

function biomePattern(biomeIdx) {
  const biomeId = state.biomeLegend[biomeIdx];
  if (biomeId === undefined) return null;
  let p = patternCache.get(biomeId);
  if (p === undefined) {
    const biome = state.biomes[biomeId];
    p = biome && biome.texture ? buildPattern(biomeId, biome.texture) : null;
    patternCache.set(biomeId, p);
  }
  return p;
}

const stairsPatternCache = new Map();  // tile type id -> CanvasPattern

// Two-square checkerboard. Unlike the biome textures this is not seeded or
// randomised -- a marker wants to be recognisable and identical everywhere,
// which is the opposite of what the terrain grain is for.
function tileTypePattern(typeId) {
  let p = stairsPatternCache.get(typeId);
  if (p !== undefined) return p;

  const style = TILE_TYPE_STYLE[typeId];
  if (!style) { stairsPatternCache.set(typeId, null); return null; }

  const off = document.createElement("canvas");
  off.width = off.height = CHECKER_SIZE * 2;
  const g = off.getContext("2d");
  g.fillStyle = style.dark;
  g.fillRect(0, 0, CHECKER_SIZE * 2, CHECKER_SIZE * 2);
  g.fillStyle = style.light;
  g.fillRect(0, 0, CHECKER_SIZE, CHECKER_SIZE);
  g.fillRect(CHECKER_SIZE, CHECKER_SIZE, CHECKER_SIZE, CHECKER_SIZE);

  p = ctx.createPattern(off, "repeat");
  stairsPatternCache.set(typeId, p);
  return p;
}

export function resetColorCache() {
  colorCache.clear();
  patternCache.clear();
  // Not strictly per-floor (the checkerboard never varies), but the patterns
  // are bound to the current context and this is the one place that drops
  // cached paint. Leaving them behind would be a stale handle waiting to bite.
  stairsPatternCache.clear();
}

// A node's colours come from its resource's category, so ore, herb and timber
// are distinguishable on the map without the renderer knowing those three
// exist. Falls back to the generic resource colours when the legend hasn't
// arrived or a node cites a category the snapshot didn't carry -- an
// unrecognised node should still draw as *a* node.
function resourceStyle(resourceId) {
  const resource = state.resources[resourceId];
  const cat = resource && state.resourceCategories[resource.category];
  return cat || { node_color: COLORS.resource, dot_color: COLORS.resourceDot };
}

// Fading overlay on the hexes an ability struck. Purely cosmetic -- the server
// has already resolved the hit; this just makes an AoE read as an area. Each
// flash carries an impact tile, an AoE radius, and a wall-clock expiry; the
// tiles in radius are enumerated from axial offsets (radius is small, 0-2), so
// this never walks the whole floor.
const FLASH_MS = 350;
function drawAbilityFlashes() {
  const now = performance.now();
  state.abilityFlashes = state.abilityFlashes.filter(f => f.until > now);
  for (const f of state.abilityFlashes) {
    const alpha = Math.max(0, (f.until - now) / FLASH_MS) * 0.55;
    for (let dq = -f.radius; dq <= f.radius; dq++) {
      const rLo = Math.max(-f.radius, -dq - f.radius);
      const rHi = Math.min(f.radius, -dq + f.radius);
      for (let dr = rLo; dr <= rHi; dr++) {
        const [cx, cy] = hexToPixel(f.q + dq, f.r + dr);
        ctx.save();
        ctx.globalAlpha = alpha;
        drawTileAt(cx, cy, "#ff8a3c", "#ffd0a8");
        ctx.restore();
      }
    }
  }
}

function drawDot(q, r, color, size = 5) {
  const [cx, cy] = hexToPixel(q, r);
  ctx.beginPath();
  ctx.arc(cx, cy, size, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

// Anchors a sprite to the ground. Without it, a flat-shaded terrain gives a
// sprite nothing to sit against and it reads as floating above the grid --
// the more so now that the tiles themselves have no gradient. Drawn under the
// sprite pass, over the terrain, and deliberately not part of the sprite
// pipeline itself: it draws no art and touches no sheet.
function drawContactShadow(q, r, scale = 1) {
  const [cx, cy] = hexToPixel(q, r);
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(cx, cy + tileSize * 0.10, tileSize * 0.46 * scale,
              tileSize * 0.22 * scale, 0, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(0, 0, 0, 0.32)";
  ctx.fill();
  ctx.restore();
}

// --- Placeholder sprite pipeline -----------------------------------------
// Monsters and players all draw from one shared placeholder sheet each; for
// monsters, visual identity comes from the server-sent `visual` block
// (config-authored, never client-guessed). The `sprite` id is resolved here
// rather than the server sending a URL, so asset paths stay a client concern.

const SPRITE_SHEETS = {
  monster_placeholder: "/art/monsters/Orc%20Captain.png",
  character_placeholder: "/art/Character/character.png",
  prefab_market_well: "/art/prefabs/market_well.png",
  prefab_market_stall: "/art/prefabs/market_stall.png",
  prefab_encampment_fire: "/art/prefabs/encampment_fire.png",
  prefab_encampment_tent: "/art/prefabs/encampment_tent.png",
};

// Frame column order, left to right, shared by every sheet. The art is
// labelled in compass terms (south, southeast, northeast, north, northwest,
// southwest); these are the same six directions in the screen-relative names
// the server and motion module already speak.
const FACINGS = ["down", "right-down", "right-up", "up", "left-up", "left-down"];

// Axial neighbour delta -> facing.
// Derived from hexToPixel: +q is right-down, +r is straight down.
export const FACING_BY_DELTA = {
  "0,1": "down",    "1,0": "right-down",  "1,-1": "right-up",
  "0,-1": "up",     "-1,0": "left-up",    "-1,1": "left-down",
};

const VISUAL_FALLBACK = {
  hue_rotate: 0, saturate: 1, brightness: 1, scale: 1, tint: COLORS.monster,
};

// Players have no per-entity visual config -- they draw the sheet untouched.
const PLAYER_VISUAL = { sprite: "character_placeholder", scale: 1 };

// id -> {img, loaded}. Sheets load once; until a sheet is ready the entity
// falls back to a dot so tiles never render empty.
const spriteCache = new Map();

function getSheet(spriteId) {
  let entry = spriteCache.get(spriteId);
  if (entry === undefined) {
    const url = SPRITE_SHEETS[spriteId];
    if (!url) return null;
    entry = { img: new Image(), loaded: false };
    entry.img.onload = () => { entry.loaded = true; };
    entry.img.src = url;
    spriteCache.set(spriteId, entry);
  }
  return entry;
}

// Prefab props (market stalls, campfires, ...) are static single-frame
// images, not directional sheets -- no facing/frame-column logic needed.
function drawPropSprite(q, r, spriteId) {
  const sheet = getSheet(spriteId);
  if (!sheet || !sheet.loaded) return;

  const [cx, cy] = hexToPixel(q, r);
  const size = tileSize * 1.6;

  ctx.save();
  ctx.translate(cx, cy);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(
    sheet.img,
    0, 0, sheet.img.width, sheet.img.height,
    -size / 2, -size * 0.72, size, size,
  );
  ctx.restore();
}

function drawSprite(q, r, visual, facing, fallbackDot) {
  const v = visual || VISUAL_FALLBACK;
  const hue = v.hue_rotate ?? 0;
  const sat = v.saturate ?? 1;
  const bri = v.brightness ?? 1;
  const scale = v.scale ?? 1;

  const sheet = getSheet(v.sprite || "monster_placeholder");
  if (!sheet || !sheet.loaded) {
    drawDot(q, r, fallbackDot, 4);
    return;
  }

  // Frame size comes from the image, not a constant: sheets differ (the orc
  // is 160px per frame, the character 128px) and a wrong guess silently
  // slices halfway into the neighbouring pose.
  const frameW = sheet.img.width / FACINGS.length;
  const frameH = sheet.img.height;

  let col = FACINGS.indexOf(facing);
  if (col < 0) col = 0;  // unknown/absent facing -> front-facing frame

  const [cx, cy] = hexToPixel(q, r);
  // Draw a bit wider than the hex so the figure reads at small tile sizes.
  const size = tileSize * 2.2 * scale;

  ctx.save();
  // Translate to the tile centre first so scaling happens in place rather
  // than dragging the sprite toward the canvas origin.
  ctx.translate(cx, cy);
  // Skip the filter entirely when there is nothing to apply -- ctx.filter is
  // not free, and players never carry modifiers.
  if (hue !== 0 || sat !== 1 || bri !== 1) {
    ctx.filter = `hue-rotate(${hue}deg) saturate(${sat}) brightness(${bri})`;
  }
  ctx.imageSmoothingEnabled = false;  // keep the pixel art crisp
  // Anchored so the figure's feet sit near the tile centre rather than the
  // sprite being centred on it -- otherwise tall sprites read as floating.
  ctx.drawImage(
    sheet.img,
    col * frameW, 0, frameW, frameH,
    -size / 2, -size * 0.72, size, size,
  );
  ctx.restore();
}

// --- Monster nameplates ---------------------------------------------------
// Name, level and a health bar floating above each living monster.
//
// LOD gating is the whole design problem here, not a refinement of it. A
// radius-64 floor holds a dozen-plus monsters, and text does not scale down
// with the tiles: at macro zoom the plates would be the same pixel size as at
// max zoom, overlapping each other and burying the terrain they sit on. So
// there are two thresholds rather than one on/off switch, and the plate sheds
// detail before it disappears:
//
//   >= NAMEPLATE_FULL_TILE_SIZE   name + level + health bar
//   >= NAMEPLATE_MIN_TILE_SIZE    health bar only (a wounded monster is still
//                                 worth spotting when the label is not)
//   below                         nothing
//
// Gated on tileSize (base fit * zoom), the same quantity the terrain texture
// and border passes gate on, so all the detail tiers drop out together as the
// player zooms out instead of at unrelated moments.
const NAMEPLATE_MIN_TILE_SIZE = 14;
const NAMEPLATE_FULL_TILE_SIZE = 22;
// Plate geometry, in fractions of tileSize so it tracks the sprites it labels.
const PLATE_BAR_W = 1.5;
const PLATE_BAR_H = 0.16;
// Sprites are drawn anchored at -size * 0.72 with size = tileSize * 2.2, so
// the art's top edge sits ~1.58 tile above centre. The bar clears it.
const PLATE_LIFT = 1.72;
const PLATE_COLORS = {
  barBack:   "rgba(0, 0, 0, 0.62)",
  barBorder: "rgba(0, 0, 0, 0.85)",
  hpHigh:    "#4fbf4f",
  hpMid:     "#d8c341",
  hpLow:     "#d84f3f",
  text:      "#e8e2d6",
  textEdge:  "rgba(0, 0, 0, 0.9)",
};
// Health colour is stepped, not a gradient: three states a player can read at
// a glance beat a continuous hue they have to interpret.
function hpColor(frac) {
  if (frac > 0.5) return PLATE_COLORS.hpHigh;
  if (frac > 0.2) return PLATE_COLORS.hpMid;
  return PLATE_COLORS.hpLow;
}

function drawNameplate(q, r, name, level, hp, maxHp, full) {
  const [cx, cy] = hexToPixel(q, r);
  const barW = tileSize * PLATE_BAR_W;
  const barH = Math.max(3, tileSize * PLATE_BAR_H);
  const barY = cy - tileSize * PLATE_LIFT;
  const frac = maxHp > 0 ? Math.max(0, Math.min(1, hp / maxHp)) : 0;

  ctx.fillStyle = PLATE_COLORS.barBack;
  ctx.fillRect(cx - barW / 2, barY, barW, barH);
  if (frac > 0) {
    ctx.fillStyle = hpColor(frac);
    ctx.fillRect(cx - barW / 2, barY, barW * frac, barH);
  }
  ctx.strokeStyle = PLATE_COLORS.barBorder;
  ctx.lineWidth = 1;
  ctx.strokeRect(cx - barW / 2 + 0.5, barY + 0.5, barW - 1, barH - 1);

  if (!full) return;

  // Outlined rather than boxed: a filled label plate per monster would stack
  // opaque rectangles over the terrain, and the stroke stays legible over both
  // the bright cel bands and the dark ones.
  const label = level ? `${name} (${level})` : name;
  const fontPx = Math.max(9, Math.round(tileSize * 0.42));
  ctx.font = `${fontPx}px monospace`;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  const textY = barY - fontPx * 0.35;
  ctx.lineWidth = Math.max(2, fontPx * 0.25);
  ctx.strokeStyle = PLATE_COLORS.textEdge;
  ctx.lineJoin = "round";
  ctx.strokeText(label, cx, textY);
  ctx.fillStyle = PLATE_COLORS.text;
  ctx.fillText(label, cx, textY);
}

export function render() {
  if (!canvas) return;
  // Guard against any resize the observer hasn't delivered yet this frame.
  syncCanvasSize();
  const w = canvas.clientWidth, h = canvas.clientHeight;
  ctx.fillStyle = COLORS.bg;
  ctx.fillRect(0, 0, w, h);

  if (state.floorNumber === null) {
    ctx.fillStyle = "#555";
    ctx.font = "14px monospace";
    ctx.textAlign = "center";
    ctx.fillText("Connecting…", w / 2, h / 2);
    return;
  }

  // Advance every entity's glide by this frame's delta before anything reads
  // a position; endFrame() at the bottom retires entities that went away.
  beginFrame();

  // Base tile size fits the floor to the viewport; zoom scales it
  const baseSize = Math.max(8, Math.min(28, Math.floor(Math.min(w, h) / (state.radius * 2.4))));
  tileSize = baseSize * state.zoom;

  // Center camera on floor origin if first load
  if (state.cameraX === 0 && state.cameraY === 0) {
    state.cameraX = w / 2;
    state.cameraY = h / 2;
  }

  // Per-tile lookups that change at runtime are resolved to tile indices once
  // per frame (there are only a handful of each), so the hot loop compares
  // integers instead of building a string key for every tile on the floor.
  // tile index -> the node's category fill, so the hot loop reads a colour
  // instead of re-resolving the resource -> category chain per tile.
  const resourceIdx = new Map();
  for (const [key, rid] of state.resourceNodes) {
    const i = state.tileIndex.get(key);
    if (i !== undefined) resourceIdx.set(i, resourceStyle(rid).node_color);
  }
  // Reserved tile types drive their own look. Resolved from the server's
  // sparse tile -> type map rather than from up_exit/down_exit, so adding a
  // reserved type server-side needs a style entry here and nothing else.
  const typeIdx = new Map();   // tile index -> type id
  for (const [key, typeId] of state.tileTypes) {
    const i = state.tileIndex.get(key);
    if (i !== undefined && TILE_TYPE_STYLE[typeId]) typeIdx.set(i, typeId);
  }
  const hoveredIdx = hoveredTile
    ? state.tileIndex.get(`${hoveredTile[0]},${hoveredTile[1]}`) ?? -1 : -1;

  // Tiles: walk canonical order so the packed arrays index directly.
  // Cull anything off-screen -- a full floor is ~12.5k hexes.
  const elev = state.elevation, rough = state.roughness, bmap = state.biomeMap;
  const n = state.tileOrder.length;
  const hasStructure = bmap.length === n && n > 0;
  const margin = tileSize * 2;
  const { ux, uy, up, road, crossing, nbr } = tileGeometry();
  const camX = state.cameraX, camY = state.cameraY;

  // Fills are always batched by colour now. Cel-quantising lightness collapses
  // the palette to a handful of values, so bucketing wins at every zoom level
  // rather than only when zoomed out -- and one path per colour is what makes
  // the extra texture and border passes below affordable.
  //
  // Borders still only draw when zoomed in: a 1px rule on a 4px hex is moire,
  // not a grid.
  const drawBorders = tileSize >= BATCH_BELOW_TILE_SIZE;
  // Texture is gated for the same reason borders are, and it matters more:
  // scaled down with the tiles the grain goes sub-pixel and resolves into a
  // moire mesh over the whole floor, which is worse than no texture at all.
  const drawTexture = tileSize >= TEXTURE_MIN_TILE_SIZE;
  const buckets = new Map();      // fill colour -> { coords, border }
  const texBuckets = new Map();   // biome index -> coords (pattern overlay)
  const typeBuckets = new Map();  // reserved tile type id -> coords
  const crossCoords = [];         // carved fords/bridges, drawn over the terrain
  const contourCoords = [];       // impassable tiles, for the stepped cap

  // Which biome indices block movement. Derived from the `passable` flag the
  // snapshot ships per biome -- the same one the client already crosses with
  // biome_map to know where it must not path. Rebuilt per frame because it is
  // a handful of entries, not per tile.
  const impassableBiome = new Uint8Array(state.biomeLegend.length);
  for (let b = 0; b < state.biomeLegend.length; b++) {
    const def = state.biomes[state.biomeLegend[b]];
    impassableBiome[b] = def && def.passable === false ? 1 : 0;
  }
  const hexSize = tileSize - 1;
  // Grain scales with the tiles so texture density reads the same at any zoom
  // (28 is the base tile size the textures were authored against).
  const texScale = tileSize / 28;

  for (let i = 0; i < n; i++) {
    // Position from precomputed unit offsets: no call, no array allocation.
    const cx = ux[i] * tileSize + camX;
    if (cx < -margin || cx > w + margin) continue;
    const cy = uy[i] * tileSize + camY;
    if (cy < -margin || cy > h + margin) continue;

    let fill = COLORS.tile;
    let border = COLORS.tileBorder;
    let texBiome = -1;

    if (hasStructure) {
      const e = elev[i] / 255;
      const ro = rough[i] / 255;

      // Layer 3: directional shading -- slope against the tile "above"
      // (decreasing r is up-screen), so ridges catch light and hollows fall
      // into shadow.
      const iUp = up[i];
      const eUp = iUp >= 0 ? elev[iUp] / 255 : e;
      const slope = e - eUp;

      // Layer 4: ambient occlusion across all six neighbours. Sum how far
      // each neighbour rises above this tile, weighted by direction so the
      // shadow falls consistently. Edge neighbours (-1) are treated as level
      // with this tile rather than as height zero -- a floor's rim is not a
      // cliff, and scoring it as one draws a dark ring around the whole map.
      // Compares raw bytes and normalises once at the end rather than dividing
      // per neighbour. Measured with the pass toggled on and off inside a
      // single page (the only comparison that holds still -- frame times here
      // drift several ms between reloads): 18.9/18.8ms with, 19.1/18.3ms
      // without, i.e. no measurable cost on a full radius-64 floor. Hand
      // unrolling the loop did not help either, so this is not a hot spot
      // worth trading clarity for.
      let occl = 0;
      const nb = i * 6;
      const eb = elev[i];
      for (let d = 0; d < 6; d++) {
        const j = nbr[nb + d];
        if (j < 0) continue;
        const rise = elev[j] - eb;
        if (rise > 0) occl += rise * OCCLUSION_WEIGHTS[d];
      }
      occl /= 255;
      if (occl > OCCLUSION_CLAMP) occl = OCCLUSION_CLAMP;

      const biome = state.biomes[state.biomeLegend[bmap[i]]];
      const baseL = (biome && biome.hsl ? biome.hsl.l : 20);
      // Cel step: snap the summed lightness to a fixed band so neighbouring
      // tiles either share a tone exactly or step to the next one, with
      // nothing in between.
      const lightness = celStep(baseL
        + (ro - 0.5) * ROUGHNESS_AMOUNT      // Layer 2: micro texture
        + (e - 0.5) * ELEVATION_AMOUNT       // absolute height
        + slope * SLOPE_AMOUNT               // Layer 3: directional
        - occl * OCCLUSION_AMOUNT);          // Layer 4: ambient occlusion
      fill = tileColor(bmap[i], lightness);
      border = tileColor(bmap[i], lightness - BORDER_DARKEN);
      texBiome = bmap[i];
    }

    const isRoad = road[i] === 1;
    if (isRoad) { fill = COLORS.road; border = "#6a5636"; texBiome = -1; }

    // Marker tiles override the procedural surface entirely -- they exist to
    // be spotted, so they opt out of the texture pass too.
    const typeId = typeIdx.get(i);
    if (typeId !== undefined) {
      const style = TILE_TYPE_STYLE[typeId];
      fill = style.base;
      border = style.border;
      texBiome = -1;
      let tb = typeBuckets.get(typeId);
      if (tb === undefined) typeBuckets.set(typeId, tb = []);
      tb.push(cx, cy);
    } else if (resourceIdx.has(i)) { fill = resourceIdx.get(i); texBiome = -1; }
    if (i === hoveredIdx) { fill = COLORS.tileHover; texBiome = -1; }

    let bucket = buckets.get(fill);
    if (bucket === undefined) buckets.set(fill, bucket = { coords: [], border });
    bucket.coords.push(cx, cy);

    // A crossing keeps whatever terrain it was carved into and takes the
    // plank overlay on top, so it still reads as continuous with the ground
    // either side of it. Roads and stairs already have their own strong
    // markers, so a crossing that coincides with one does not double up.
    if (crossing[i] === 1 && !isRoad && typeId === undefined) {
      crossCoords.push(cx, cy);
    }

    // Contour tiers go on barrier tiles only, and only where they are big
    // enough to resolve -- see the pass below.
    if (drawTexture && texBiome >= 0 && impassableBiome[texBiome] === 1) {
      contourCoords.push(cx, cy);
    }

    if (drawTexture && texBiome >= 0) {
      let tb = texBuckets.get(texBiome);
      if (tb === undefined) texBuckets.set(texBiome, tb = []);
      tb.push(cx, cy);
    }
  }

  // Pass 1: flat cel-shaded fills, one path per colour.
  for (const [fill, bucket] of buckets) {
    ctx.beginPath();
    const coords = bucket.coords;
    for (let k = 0; k < coords.length; k += 2) {
      addHex(coords[k], coords[k + 1], hexSize);
    }
    ctx.fillStyle = fill;
    ctx.fill();
  }

  // Pass 2: procedural surface texture, one path per biome. The pattern is
  // transform-locked to the world rather than the canvas, so the grain stays
  // stuck to the terrain when the camera pans instead of swimming across it.
  for (const [biomeIdx, coords] of texBuckets) {
    const pattern = biomePattern(biomeIdx);
    if (!pattern) continue;
    if (pattern.setTransform) {
      pattern.setTransform(new DOMMatrix()
        .translate(camX % (PATTERN_SIZE * texScale), camY % (PATTERN_SIZE * texScale))
        .scale(texScale));
    }
    ctx.beginPath();
    for (let k = 0; k < coords.length; k += 2) {
      addHex(coords[k], coords[k + 1], hexSize);
    }
    ctx.fillStyle = pattern;
    ctx.fill();
  }

  // Pass 2c: contour tiers on barrier terrain. Two concentric insets filled
  // with a flat translucent white, which steps the tile up towards a bright
  // cap and reads as a hand-drawn contour peak.
  //
  // Tinting rather than recolouring is what keeps this batched: the tiers are
  // colour-independent, so every barrier tile on the floor -- mountain, dense
  // forest, any future one -- draws in exactly two paths total, instead of one
  // per (biome, lightness) pair. It also means the tiers compose with whatever
  // the occlusion pass did to the base fill underneath, so a shadowed cliff
  // steps up from a darker plate than a lit one, for free.
  //
  // Gated with the terrain grain and for the same reason: at 8px a 0.5-scale
  // inset is two pixels of ring and turns to moire. The occlusion term above
  // is what carries the relief at that zoom -- it is a colour change, so it
  // survives where geometry cannot.
  if (contourCoords.length) {
    for (const tier of CONTOUR_TIERS) {
      ctx.beginPath();
      const r = hexSize * tier.scale;
      for (let k = 0; k < contourCoords.length; k += 2) {
        addHex(contourCoords[k], contourCoords[k + 1], r);
      }
      ctx.fillStyle = tier.fill;
      ctx.fill();
    }
  }

  // Pass 2a: crossings -- the bridges and fords the generator carved through
  // barrier terrain so the exits stay reachable. Drawn after the terrain
  // texture and before entities: the whole point of a guaranteed route is
  // that the player can see it without walking the shoreline to find it, so
  // it must sit on top of the water ripples rather than under them.
  //
  // Unlike the terrain grain this is NOT gated on zoom. Texture zoomed out is
  // decoration and turns to moire, but a crossing is navigation -- it is most
  // useful precisely when the player is looking at the whole floor deciding
  // where to walk. Plank count is fixed and their spacing scales, so the mark
  // degrades to a few legible bars instead of mush.
  if (crossCoords.length) {
    const planks = 3;
    ctx.strokeStyle = COLORS.crossing;
    ctx.lineWidth = Math.max(1, tileSize * 0.14);
    ctx.lineCap = "butt";
    ctx.beginPath();
    for (let k = 0; k < crossCoords.length; k += 2) {
      const cx = crossCoords[k], cy = crossCoords[k + 1];
      const half = hexSize * 0.62;   // stop short of the hex corners
      const gap = (hexSize * 1.1) / (planks + 1);
      for (let p = 1; p <= planks; p++) {
        const y = cy - hexSize * 0.55 + gap * p;
        ctx.moveTo(cx - half, y);
        ctx.lineTo(cx + half, y);
      }
    }
    ctx.stroke();
  }

  // Pass 2b: reserved tile types (stairs). Same world-locked transform as the
  // terrain texture so the checker sits on the tile instead of sliding across
  // it while the camera pans.
  //
  // Gated at the same zoom threshold as the terrain grain, and for the same
  // reason: scaled down, the squares go sub-pixel and turn to mush. The solid
  // base fill underneath is already a stark green/blue against the terrain, so
  // zoomed out the stairs stay findable -- they just stop being checkered.
  if (drawTexture) {
    for (const [typeId, coords] of typeBuckets) {
      const pattern = tileTypePattern(typeId);
      if (!pattern) continue;
      if (pattern.setTransform) {
        const period = CHECKER_SIZE * 2 * texScale;
        pattern.setTransform(new DOMMatrix()
          .translate(camX % period, camY % period)
          .scale(texScale));
      }
      ctx.beginPath();
      for (let k = 0; k < coords.length; k += 2) {
        addHex(coords[k], coords[k + 1], hexSize);
      }
      ctx.fillStyle = pattern;
      ctx.fill();
    }
  }

  // Pass 3: cell borders. Drawn from each tile's own darkened colour, so the
  // separation is a shade of the terrain rather than a grey rule laid over it.
  if (drawBorders) {
    ctx.lineWidth = BORDER_WIDTH;
    for (const [, bucket] of buckets) {
      ctx.beginPath();
      const coords = bucket.coords;
      for (let k = 0; k < coords.length; k += 2) {
        addHex(coords[k], coords[k + 1], hexSize);
      }
      ctx.strokeStyle = bucket.border;
      ctx.stroke();
    }
  }

  // Resource dots, coloured by gathering category.
  for (const [key, rid] of state.resourceNodes) {
    const [q, rv] = key.split(",").map(Number);
    drawDot(q, rv, resourceStyle(rid).dot_color, 3);
  }

  // Prefab props (drawn under entities, same as tiles, so nothing is clipped
  // by a later sprite).
  for (const [key, spriteId] of state.prefabTiles) {
    const [q, rv] = key.split(",").map(Number);
    drawContactShadow(q, rv, 0.8);
    drawPropSprite(q, rv, spriteId);
  }

  drawAbilityFlashes();

  // Monsters and players share one sprite pass.
  // The tinted hex marks the tile an entity logically occupies, so it stays
  // on the grid; only the sprite glides between centres.
  // Sprites are taller than a hex, so paint back-to-front by screen Y --
  // monsters and players sorted together, since they overlap each other just
  // as readily. Tiles are laid down first so no sprite is clipped by a later
  // tile.
  const sprites = [];

  for (const [mid, m] of state.monsters) {
    if (!m.alive) continue;
    const [q, rv] = m.tile;
    drawTile(q, rv, (m.visual && m.visual.tint) || COLORS.monster, "#5a2020");
    const at = resolveMotion(`m:${mid}`, m.tile, m.facing);
    at.visual = m.visual;
    at.dot = COLORS.monsterDot;
    // Carried on the resolved motion position, not the logical tile, so the
    // plate glides with the sprite it belongs to rather than snapping between
    // hex centres a step ahead of it.
    at.plate = { name: m.display_name || m.template_id, level: m.level,
                 hp: m.hp, maxHp: m.max_hp };
    sprites.push(at);
  }

  for (const [pid, p] of state.players) {
    const isSelf = pid === state.playerId;
    const [q, rv] = p.tile;
    drawTile(q, rv, isSelf ? COLORS.selfPlayer : COLORS.otherPlayer,
             isSelf ? "#205a20" : "#20205a");
    const at = resolveMotion(`p:${pid}`, p.tile, p.facing);
    at.visual = PLAYER_VISUAL;
    at.dot = isSelf ? COLORS.selfDot : COLORS.otherDot;
    sprites.push(at);
  }

  sprites.sort((a, b) => hexToPixel(a.q, a.r)[1] - hexToPixel(b.q, b.r)[1]);
  // Every shadow first, then every sprite: drawn interleaved, a sprite standing
  // in front would have the next entity's shadow painted over its feet.
  for (const s of sprites) {
    drawContactShadow(s.q, s.r, (s.visual && s.visual.scale) || 1);
  }
  for (const s of sprites) {
    drawSprite(s.q, s.r, s.visual, s.facing, s.dot);
  }

  // Nameplates last among the entity passes, so a monster standing in front
  // never paints over the plate of one behind it -- the sprites are depth
  // sorted, but the plates sit above every sprite and must not be.
  if (tileSize >= NAMEPLATE_MIN_TILE_SIZE) {
    const full = tileSize >= NAMEPLATE_FULL_TILE_SIZE;
    for (const s of sprites) {
      if (!s.plate) continue;
      drawNameplate(s.q, s.r, s.plate.name, s.plate.level,
                    s.plate.hp, s.plate.maxHp, full);
    }
  }

  // Reserved-tile glyphs, drawn last so no sprite hides the stairs. Direction
  // is the one thing the checkerboard cannot convey on its own.
  ctx.font = `${Math.max(8, tileSize * 0.4)}px monospace`;
  ctx.textAlign = "center";
  for (const [key, typeId] of state.tileTypes) {
    const style = TILE_TYPE_STYLE[typeId];
    if (!style) continue;
    const [q, rv] = key.split(",").map(Number);
    const [cx, cy] = hexToPixel(q, rv);
    // Backing disc: the glyph is the only thing distinguishing up from down,
    // and drawn bare it disappears into the checker's light squares.
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(5, tileSize * 0.30), 0, Math.PI * 2);
    ctx.fillStyle = style.base;
    ctx.fill();
    ctx.fillStyle = style.glyphColor;
    ctx.fillText(style.glyph, cx, cy + 4);
  }

  endFrame();
}

function onMouseMove(e) {
  const rect = canvas.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const py = e.clientY - rect.top;
  hoveredTile = pixelToHex(px, py);
}

export function getHoveredTile() { return hoveredTile; }
export function getTileSize() { return tileSize; }
export function hexToPixelPublic(q, r) { return hexToPixel(q, r); }
