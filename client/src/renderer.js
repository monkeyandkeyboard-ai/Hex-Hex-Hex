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

export function resetColorCache() {
  colorCache.clear();
}

function drawDot(q, r, color, size = 5) {
  const [cx, cy] = hexToPixel(q, r);
  ctx.beginPath();
  ctx.arc(cx, cy, size, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

// --- Placeholder sprite pipeline -----------------------------------------
// Monsters and players all draw from one shared placeholder sheet each; for
// monsters, visual identity comes from the server-sent `visual` block
// (config-authored, never client-guessed). The `sprite` id is resolved here
// rather than the server sending a URL, so asset paths stay a client concern.

const SPRITE_SHEETS = {
  monster_placeholder: "/art/monsters/Orc%20Captain.png",
  character_placeholder: "/art/Character/character.png",
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

  // Below this tile size a screenful is thousands of hexes and the 1px border
  // is visual noise, so fills are batched by colour and borders dropped.
  // Above it, tile counts are low and per-hex fill+stroke is both cheap and
  // better looking. Same output either way at the sizes that matter.
  const batchFills = tileSize < BATCH_BELOW_TILE_SIZE;
  const buckets = batchFills ? new Map() : null;
  const hexSize = tileSize - 1;

  for (let i = 0; i < n; i++) {
    // Position from precomputed unit offsets: no call, no array allocation.
    const cx = ux[i] * tileSize + camX;
    if (cx < -margin || cx > w + margin) continue;
    const cy = uy[i] * tileSize + camY;
    if (cy < -margin || cy > h + margin) continue;

    let fill = COLORS.tile;

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
      const lightness = baseL
        + (ro - 0.5) * ROUGHNESS_AMOUNT      // Layer 2: micro texture
        + (e - 0.5) * ELEVATION_AMOUNT       // absolute height
        + slope * SLOPE_AMOUNT;              // Layer 3: directional
      fill = tileColor(bmap[i], lightness);
    }

    const isRoad = road[i] === 1;
    if (isRoad) fill = COLORS.road;

    if (i === upExitIdx) fill = COLORS.upExit;
    else if (i === downExitIdx) fill = COLORS.downExit;
    else if (resourceIdx.has(i)) fill = COLORS.resource;
    if (i === hoveredIdx) fill = COLORS.tileHover;

    if (batchFills) {
      // Zoomed out: bucket by colour and paint each colour in one path.
      // Per-hex fill+stroke costs ~72ms for a screenful at this size, almost
      // all of it stroking; one path per colour is ~11ms. Borders are dropped
      // here on purpose -- a 1px rule on a 4px hex is moire, not a grid.
      let bucket = buckets.get(fill);
      if (bucket === undefined) buckets.set(fill, bucket = []);
      bucket.push(cx, cy);
    } else {
      // Zoomed in: few enough tiles that per-hex painting is cheap, and the
      // border genuinely reads. Layer 4: crisp fixed-width border.
      drawTileAt(cx, cy, fill, isRoad ? "#6a5636" : COLORS.tileBorder);
    }
  }

  if (batchFills) {
    for (const [fill, coords] of buckets) {
      ctx.beginPath();
      for (let k = 0; k < coords.length; k += 2) {
        addHex(coords[k], coords[k + 1], hexSize);
      }
      ctx.fillStyle = fill;
      ctx.fill();
    }
  }

  // Resource dots
  for (const [key] of state.resourceNodes) {
    const [q, rv] = key.split(",").map(Number);
    drawDot(q, rv, COLORS.resourceDot, 3);
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
