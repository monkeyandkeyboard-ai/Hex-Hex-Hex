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

function hexPath(cx, cy, size) {
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const angle = Math.PI / 180 * (60 * i);
    const x = cx + size * Math.cos(angle);
    const y = cy + size * Math.sin(angle);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  }
  ctx.closePath();
}

function drawTile(q, r, fill, stroke) {
  const [cx, cy] = hexToPixel(q, r);
  hexPath(cx, cy, tileSize - 1);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.strokeStyle = stroke;
  ctx.lineWidth = BORDER_WIDTH;
  ctx.stroke();
}

// --- Procedural HSL stack -------------------------------------------------
// Layer 1 base biome hue/sat/lightness, Layer 2 roughness micro-texture,
// Layer 3 directional elevation shading. Colour strings are cached by
// (biome, quantised lightness) since building 12k hsl() strings per frame
// would dominate the frame budget.

const BORDER_WIDTH = 1;
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
// Every monster draws the same placeholder spritesheet; all visual identity
// comes from the server-sent `visual` block (config-authored, never
// client-guessed). The `sprite` id is resolved here rather than the server
// sending a URL, so asset paths stay a client concern.

const SPRITE_SHEETS = {
  monster_placeholder: "/art/monsters/Orc%20Captain.png",
};

// Frame column order in the sheet, left to right.
const FACINGS = ["down", "right-down", "right-up", "up", "left-up", "left-down"];
const FRAME_SIZE = 160;

// Axial neighbour delta -> facing, for whenever monsters start moving.
// Derived from hexToPixel: +q is right-down, +r is straight down.
export const FACING_BY_DELTA = {
  "0,1": "down",    "1,0": "right-down",  "1,-1": "right-up",
  "0,-1": "up",     "-1,0": "left-up",    "-1,1": "left-down",
};

const VISUAL_FALLBACK = {
  hue_rotate: 0, saturate: 1, brightness: 1, scale: 1, tint: COLORS.monster,
};

// id -> {img, loaded}. Sheets load once; until a sheet is ready the monster
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

function drawMonsterSprite(q, r, visual, facing) {
  const v = visual || VISUAL_FALLBACK;
  const hue = v.hue_rotate ?? 0;
  const sat = v.saturate ?? 1;
  const bri = v.brightness ?? 1;
  const scale = v.scale ?? 1;

  const sheet = getSheet(v.sprite || "monster_placeholder");
  if (!sheet || !sheet.loaded) {
    drawDot(q, r, COLORS.monsterDot, 4);
    return;
  }

  let col = FACINGS.indexOf(facing);
  if (col < 0) col = 0;  // unknown/absent facing -> front-facing frame

  const [cx, cy] = hexToPixel(q, r);
  // Draw a bit wider than the hex so the figure reads at small tile sizes.
  const size = tileSize * 2.2 * scale;

  ctx.save();
  // Translate to the tile centre first so scaling happens in place rather
  // than dragging the sprite toward the canvas origin.
  ctx.translate(cx, cy);
  ctx.filter = `hue-rotate(${hue}deg) saturate(${sat}) brightness(${bri})`;
  ctx.imageSmoothingEnabled = false;  // keep the pixel art crisp
  // Anchored so the figure's feet sit near the tile centre rather than the
  // sprite being centred on it -- otherwise tall sprites read as floating.
  ctx.drawImage(
    sheet.img,
    col * FRAME_SIZE, 0, FRAME_SIZE, FRAME_SIZE,
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

  const resourceKeys = new Set(
    [...state.resourceNodes.keys()]
  );

  // Tiles: walk canonical order so the packed arrays index directly.
  // Cull anything off-screen -- a full floor is ~12.5k hexes.
  const elev = state.elevation, rough = state.roughness, bmap = state.biomeMap;
  const hasStructure = bmap.length === state.tileOrder.length && bmap.length > 0;
  const margin = tileSize * 2;

  for (let i = 0; i < state.tileOrder.length; i++) {
    const [q, rv] = state.tileOrder[i];
    const [cx, cy] = hexToPixel(q, rv);
    if (cx < -margin || cx > w + margin || cy < -margin || cy > h + margin) continue;

    const key = `${q},${rv}`;
    let fill = COLORS.tile;

    if (hasStructure) {
      const e = elev[i] / 255;
      const ro = rough[i] / 255;

      // Layer 3: directional shading -- slope against the tile "above"
      // (decreasing r is up-screen), so ridges catch light and hollows fall
      // into shadow.
      const iUp = state.tileIndex.get(`${q},${rv - 1}`);
      const eUp = iUp !== undefined ? elev[iUp] / 255 : e;
      const slope = e - eUp;

      const biome = state.biomes[state.biomeLegend[bmap[i]]];
      const baseL = (biome && biome.hsl ? biome.hsl.l : 20);
      const lightness = baseL
        + (ro - 0.5) * ROUGHNESS_AMOUNT      // Layer 2: micro texture
        + (e - 0.5) * ELEVATION_AMOUNT       // absolute height
        + slope * SLOPE_AMOUNT;              // Layer 3: directional
      fill = tileColor(bmap[i], lightness);
    }

    const isRoad = state.roads.has(key);
    if (isRoad) fill = COLORS.road;

    if (state.upExit && q === state.upExit[0] && rv === state.upExit[1]) fill = COLORS.upExit;
    else if (state.downExit && q === state.downExit[0] && rv === state.downExit[1]) fill = COLORS.downExit;
    else if (resourceKeys.has(key)) fill = COLORS.resource;

    const isHovered = hoveredTile && hoveredTile[0] === q && hoveredTile[1] === rv;
    // Layer 4: crisp fixed-width border anchoring the grid.
    drawTile(q, rv, isHovered ? COLORS.tileHover : fill, isRoad ? "#6a5636" : COLORS.tileBorder);
  }

  // Resource dots
  for (const [key] of state.resourceNodes) {
    const [q, rv] = key.split(",").map(Number);
    drawDot(q, rv, COLORS.resourceDot, 3);
  }

  // Monsters — placeholder sprite on a tinted tile.
  // The tinted hex marks the tile the monster logically occupies, so it stays
  // on the grid; only the sprite glides between centres.
  // Sprites are taller than a hex, so paint back-to-front by screen Y:
  // in Map order a far monster could otherwise overlap a nearer one. Tiles
  // are laid down in a first pass so no sprite is clipped by a later tile.
  const drawnMonsters = [];
  for (const [mid, m] of state.monsters) {
    if (!m.alive) continue;
    const [q, rv] = m.tile;
    drawTile(q, rv, (m.visual && m.visual.tint) || COLORS.monster, "#5a2020");
    drawnMonsters.push(resolveMotion(`m:${mid}`, m.tile, m.facing));
    drawnMonsters[drawnMonsters.length - 1].visual = m.visual;
  }
  drawnMonsters.sort((a, b) => hexToPixel(a.q, a.r)[1] - hexToPixel(b.q, b.r)[1]);
  for (const m of drawnMonsters) {
    drawMonsterSprite(m.q, m.r, m.visual, m.facing);
  }

  // Other players — tile marks the occupied hex, dot glides between centres
  for (const [pid, p] of state.players) {
    if (pid === state.playerId) continue;
    const [q, rv] = p.tile;
    drawTile(q, rv, COLORS.otherPlayer, "#20205a");
    const at = resolveMotion(`p:${pid}`, p.tile, p.facing);
    drawDot(at.q, at.r, COLORS.otherDot, 4);
  }

  // Self
  const self = state.players.get(state.playerId);
  if (self) {
    const [q, rv] = self.tile;
    drawTile(q, rv, COLORS.selfPlayer, "#205a20");
    const at = resolveMotion(`p:${state.playerId}`, self.tile, self.facing);
    drawDot(at.q, at.r, COLORS.selfDot, 5);
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
