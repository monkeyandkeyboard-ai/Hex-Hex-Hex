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
  resource:     "#3a2a10",
  resourceDot:  "#c8901a",
  monster:      "#3a1010",
  monsterDot:   "#e05050",
  selfPlayer:   "#103a10",
  selfDot:      "#50e050",
  otherPlayer:  "#10103a",
  otherDot:     "#5050e0",
};

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
}

const ZOOM_MIN = 0.3, ZOOM_MAX = 5.0, ZOOM_STEP = 1.15;

function onWheel(e) {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const px = e.clientX - rect.left;
  const py = e.clientY - rect.top;

  const oldZoom = state.zoom;
  const newZoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN,
    e.deltaY < 0 ? oldZoom * ZOOM_STEP : oldZoom / ZOOM_STEP));
  if (newZoom === oldZoom) return;

  // Keep the world point under the cursor fixed: world scale is
  // proportional to zoom, so scale the cursor->camera offset by the ratio.
  const k = newZoom / oldZoom;
  state.cameraX = px - (px - state.cameraX) * k;
  state.cameraY = py - (py - state.cameraY) * k;
  state.zoom = newZoom;
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

let geometry = null;  // { order, ux, uy, up, road }

function tileGeometry() {
  const order = state.tileOrder;
  if (geometry !== null && geometry.order === order) return geometry;

  const n = order.length;
  const ux = new Float32Array(n);
  const uy = new Float32Array(n);
  const up = new Int32Array(n);
  const road = new Uint8Array(n);
  const SQRT3 = Math.sqrt(3);

  for (let i = 0; i < n; i++) {
    const q = order[i][0], r = order[i][1];
    ux[i] = 1.5 * q;
    uy[i] = SQRT3 * (q / 2 + r);
    const iUp = state.tileIndex.get(`${q},${r - 1}`);
    up[i] = iUp === undefined ? -1 : iUp;
    road[i] = state.roads.has(`${q},${r}`) ? 1 : 0;
  }

  geometry = { order, ux, uy, up, road };
  return geometry;
}

/** Drop cached geometry (new floor, or roads changed). */
export function resetGeometry() {
  geometry = null;
}

// --- Procedural HSL stack -------------------------------------------------
// Layer 1 base biome hue/sat/lightness, Layer 2 roughness micro-texture,
// Layer 3 directional elevation shading. Colour strings are cached by
// (biome, quantised lightness) since building 12k hsl() strings per frame
// would dominate the frame budget.

const BORDER_WIDTH = 1;
// Below this tile size, a screenful is thousands of hexes and per-hex
// stroking dominates the frame; above it, tile counts are low enough that
// per-hex painting is cheap and the 1px border actually reads. Measured:
// at tileSize 8 per-hex costs ~25ms/frame vs ~10ms batched.
const BATCH_BELOW_TILE_SIZE = 14;
const ROUGHNESS_AMOUNT = 7;   // lightness swing from micro-texture
const ELEVATION_AMOUNT = 10;  // lightness swing from absolute height
const SLOPE_AMOUNT = 30;      // directional shading strength

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

export function resetColorCache() {
  colorCache.clear();
  patternCache.clear();
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
  const resourceIdx = new Set();
  for (const key of state.resourceNodes.keys()) {
    const i = state.tileIndex.get(key);
    if (i !== undefined) resourceIdx.add(i);
  }
  const upExitIdx = state.upExit
    ? state.tileIndex.get(`${state.upExit[0]},${state.upExit[1]}`) ?? -1 : -1;
  const downExitIdx = state.downExit
    ? state.tileIndex.get(`${state.downExit[0]},${state.downExit[1]}`) ?? -1 : -1;
  const hoveredIdx = hoveredTile
    ? state.tileIndex.get(`${hoveredTile[0]},${hoveredTile[1]}`) ?? -1 : -1;

  // Tiles: walk canonical order so the packed arrays index directly.
  // Cull anything off-screen -- a full floor is ~12.5k hexes.
  const elev = state.elevation, rough = state.roughness, bmap = state.biomeMap;
  const n = state.tileOrder.length;
  const hasStructure = bmap.length === n && n > 0;
  const margin = tileSize * 2;
  const { ux, uy, up, road } = tileGeometry();
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

      const biome = state.biomes[state.biomeLegend[bmap[i]]];
      const baseL = (biome && biome.hsl ? biome.hsl.l : 20);
      // Cel step: snap the summed lightness to a fixed band so neighbouring
      // tiles either share a tone exactly or step to the next one, with
      // nothing in between.
      const lightness = celStep(baseL
        + (ro - 0.5) * ROUGHNESS_AMOUNT      // Layer 2: micro texture
        + (e - 0.5) * ELEVATION_AMOUNT       // absolute height
        + slope * SLOPE_AMOUNT);             // Layer 3: directional
      fill = tileColor(bmap[i], lightness);
      border = tileColor(bmap[i], lightness - BORDER_DARKEN);
      texBiome = bmap[i];
    }

    const isRoad = road[i] === 1;
    if (isRoad) { fill = COLORS.road; border = "#6a5636"; texBiome = -1; }

    // Marker tiles override the procedural surface entirely -- they exist to
    // be spotted, so they opt out of the texture pass too.
    if (i === upExitIdx) { fill = COLORS.upExit; texBiome = -1; }
    else if (i === downExitIdx) { fill = COLORS.downExit; texBiome = -1; }
    else if (resourceIdx.has(i)) { fill = COLORS.resource; texBiome = -1; }
    if (i === hoveredIdx) { fill = COLORS.tileHover; texBiome = -1; }

    let bucket = buckets.get(fill);
    if (bucket === undefined) buckets.set(fill, bucket = { coords: [], border });
    bucket.coords.push(cx, cy);

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

  // Resource dots
  for (const [key] of state.resourceNodes) {
    const [q, rv] = key.split(",").map(Number);
    drawDot(q, rv, COLORS.resourceDot, 3);
  }

  // Prefab props (drawn under entities, same as tiles, so nothing is clipped
  // by a later sprite).
  for (const [key, spriteId] of state.prefabTiles) {
    const [q, rv] = key.split(",").map(Number);
    drawContactShadow(q, rv, 0.8);
    drawPropSprite(q, rv, spriteId);
  }

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

  // Exit labels
  if (state.upExit) {
    const [cx, cy] = hexToPixel(...state.upExit);
    ctx.fillStyle = "#50c850";
    ctx.font = `${Math.max(8, tileSize * 0.4)}px monospace`;
    ctx.textAlign = "center";
    ctx.fillText("▲", cx, cy + 4);
  }
  if (state.downExit) {
    const [cx, cy] = hexToPixel(...state.downExit);
    ctx.fillStyle = "#5050c8";
    ctx.font = `${Math.max(8, tileSize * 0.4)}px monospace`;
    ctx.textAlign = "center";
    ctx.fillText("▼", cx, cy + 4);
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
