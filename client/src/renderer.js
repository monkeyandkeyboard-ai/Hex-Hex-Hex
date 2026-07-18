// Canvas2D hex renderer. Reads from state, draws, never modifies state.
// Hex layout: flat-top pointy-top axial. Size = pixel radius of a tile.

import { state } from "./state.js";

const COLORS = {
  bg:           "#0d0d0f",
  tile:         "#1a1a1f",
  tileBorder:   "#252530",
  tileHover:    "#252535",
  upExit:       "#1a3a1a",
  downExit:     "#1a1a3a",
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
  resize();
  window.addEventListener("resize", resize);
  canvas.addEventListener("mousemove", onMouseMove);
  canvas.addEventListener("mouseleave", () => { hoveredTile = null; });
}

function resize() {
  canvas.width  = canvas.clientWidth  * devicePixelRatio;
  canvas.height = canvas.clientHeight * devicePixelRatio;
  ctx.scale(devicePixelRatio, devicePixelRatio);
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
  ctx.lineWidth = 0.5;
  ctx.stroke();
}

function drawDot(q, r, color, size = 5) {
  const [cx, cy] = hexToPixel(q, r);
  ctx.beginPath();
  ctx.arc(cx, cy, size, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();
}

export function render() {
  if (!canvas) return;
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

  // Adjust zoom/tile size based on floor radius
  tileSize = Math.max(8, Math.min(28, Math.floor(Math.min(w, h) / (state.radius * 2.4))));

  // Center camera on floor origin if first load
  if (state.cameraX === 0 && state.cameraY === 0) {
    state.cameraX = w / 2;
    state.cameraY = h / 2;
  }

  const resourceKeys = new Set(
    [...state.resourceNodes.keys()]
  );

  // Draw all tiles (simple approach: iterate known tiles from radius)
  const r = state.radius;
  for (let q = -r; q <= r; q++) {
    const rLo = Math.max(-r, -q - r);
    const rHi = Math.min(r, -q + r);
    for (let rv = rLo; rv <= rHi; rv++) {
      const key = `${q},${rv}`;
      let fill = COLORS.tile;

      if (state.upExit && q === state.upExit[0] && rv === state.upExit[1]) fill = COLORS.upExit;
      else if (state.downExit && q === state.downExit[0] && rv === state.downExit[1]) fill = COLORS.downExit;
      else if (resourceKeys.has(key)) fill = COLORS.resource;

      const isHovered = hoveredTile && hoveredTile[0] === q && hoveredTile[1] === rv;
      drawTile(q, rv, isHovered ? COLORS.tileHover : fill, COLORS.tileBorder);
    }
  }

  // Resource dots
  for (const [key] of state.resourceNodes) {
    const [q, rv] = key.split(",").map(Number);
    drawDot(q, rv, COLORS.resourceDot, 3);
  }

  // Monsters
  for (const [, m] of state.monsters) {
    if (!m.alive) continue;
    const [q, rv] = m.tile;
    drawTile(q, rv, COLORS.monster, "#5a2020");
    drawDot(q, rv, COLORS.monsterDot, 4);
  }

  // Other players
  for (const [pid, p] of state.players) {
    if (pid === state.playerId) continue;
    const [q, rv] = p.tile;
    drawTile(q, rv, COLORS.otherPlayer, "#20205a");
    drawDot(q, rv, COLORS.otherDot, 4);
  }

  // Self
  const self = state.players.get(state.playerId);
  if (self) {
    const [q, rv] = self.tile;
    drawTile(q, rv, COLORS.selfPlayer, "#205a20");
    drawDot(q, rv, COLORS.selfDot, 5);
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
