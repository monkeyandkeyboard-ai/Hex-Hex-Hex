// Per-skill hexagonal allocation tree, rendered in a modal overlay.
//
// Scope note: nodes are structural placeholders only. This module owns the
// grid, the traversal rules and the point budget -- it deliberately grants no
// stats and sends nothing to the server, so nothing here can affect combat.
//
// Coordinates are cube (x + y + z === 0). The path is a self-avoiding walk
// from the centre outwards: each new node must touch the current head, and a
// node already on the path can never be re-entered.

import { state } from "./state.js";

const RADIUS = 3;                 // 1 + 6 + 12 + 18 = 37 hexes
const HEX_SIZE = 30;              // circumradius in px, pointy-top
const SQRT3 = Math.sqrt(3);

// The six cube-coordinate neighbour offsets, in clockwise order.
const DIRECTIONS = [
  [1, -1, 0], [1, 0, -1], [0, 1, -1],
  [-1, 1, 0], [-1, 0, 1], [0, -1, 1],
];

const CENTER_KEY = "0,0,0";

let overlay, gridEl, titleEl, budgetEl;

// Allocation is tracked per skill and never shared: `paths[skill]` is the
// ordered list of cube keys, always starting at the centre.
const paths = {};
let activeSkill = null;

function key(x, y, z) {
  return `${x},${y},${z}`;
}

function parseKey(k) {
  return k.split(",").map(Number);
}

/** Every cube coordinate within `RADIUS` of the origin. */
function generateGrid() {
  const cells = [];
  for (let x = -RADIUS; x <= RADIUS; x++) {
    const lo = Math.max(-RADIUS, -x - RADIUS);
    const hi = Math.min(RADIUS, -x + RADIUS);
    for (let y = lo; y <= hi; y++) {
      cells.push([x, y, -x - y]);
    }
  }
  return cells;
}

const GRID = generateGrid();
const GRID_KEYS = new Set(GRID.map(([x, y, z]) => key(x, y, z)));

/** Pixel centre of a cube coordinate, pointy-top layout, origin at (0,0). */
function toPixel(x, z) {
  return {
    px: HEX_SIZE * SQRT3 * (x + z / 2),
    py: HEX_SIZE * 1.5 * z,
  };
}

function hexPoints(cx, cy) {
  const pts = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    pts.push(`${cx + HEX_SIZE * Math.cos(angle)},${cy + HEX_SIZE * Math.sin(angle)}`);
  }
  return pts.join(" ");
}

// --- rules -----------------------------------------------------------------

/**
 * Total points spent once `n` nodes have been allocated beyond the centre.
 * Triangular: 1, 3, 6, 10, 15, 21... The centre is the free starting seat.
 */
function cumulativeCost(n) {
  return (n * (n + 1)) / 2;
}

function skillLevel(skill) {
  const s = state.selfSkills[skill];
  if (s === undefined) return 0;
  return typeof s === "object" ? s.level : s;
}

function pathFor(skill) {
  if (!paths[skill]) paths[skill] = [CENTER_KEY];
  return paths[skill];
}

function neighbours(k) {
  const [x, y, z] = parseKey(k);
  return DIRECTIONS.map(([dx, dy, dz]) => key(x + dx, y + dy, z + dz));
}

/** Keys the player could legally click right now, budget included. */
function validNextSteps(skill) {
  const path = pathFor(skill);
  const spent = path.length;                       // nodes beyond centre + 1
  if (cumulativeCost(spent) > skillLevel(skill)) return new Set();

  const onPath = new Set(path);
  return new Set(
    neighbours(path[path.length - 1]).filter((k) => GRID_KEYS.has(k) && !onPath.has(k)),
  );
}

function allocate(skill, k) {
  if (!validNextSteps(skill).has(k)) return;
  pathFor(skill).push(k);
  render();
}

// --- rendering -------------------------------------------------------------

function render() {
  if (!activeSkill) return;
  const skill = activeSkill;
  const path = pathFor(skill);
  const onPath = new Set(path);
  const head = path[path.length - 1];
  const valid = validNextSteps(skill);

  const level = skillLevel(skill);
  const spent = cumulativeCost(path.length - 1);
  const nextCost = cumulativeCost(path.length);

  titleEl.textContent = skill.replace(/_/g, " ");
  budgetEl.textContent =
    `${spent} / ${level} points spent` +
    (valid.size ? `  ·  next node costs ${nextCost - spent}` : "  ·  no affordable moves");

  const cells = GRID.map(([x, y, z]) => {
    const k = key(x, y, z);
    const { px, py } = toPixel(x, z);
    let cls = "hex";
    if (k === head) cls += " head";
    else if (onPath.has(k)) cls += " allocated";
    else if (valid.has(k)) cls += " valid";
    const idx = onPath.has(k) ? path.indexOf(k) : "";
    return `<g class="${cls}" data-key="${k}">
      <polygon points="${hexPoints(px, py)}"></polygon>
      <text x="${px}" y="${py + 4}" text-anchor="middle">${idx === 0 ? "◆" : idx}</text>
    </g>`;
  }).join("");

  const extent = HEX_SIZE * SQRT3 * (RADIUS + 1);
  gridEl.innerHTML =
    `<svg viewBox="${-extent} ${-extent} ${extent * 2} ${extent * 2}">${cells}</svg>`;
}

// --- public API ------------------------------------------------------------

export function initSkillTree() {
  overlay  = document.getElementById("skilltree-overlay");
  gridEl   = document.getElementById("skilltree-grid");
  titleEl  = document.getElementById("skilltree-title");
  budgetEl = document.getElementById("skilltree-budget");

  document.getElementById("skilltree-close").addEventListener("click", closeSkillTree);
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeSkillTree();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && overlay.style.display === "flex") closeSkillTree();
  });

  gridEl.addEventListener("click", (e) => {
    const g = e.target.closest("g.hex");
    if (g && activeSkill) allocate(activeSkill, g.dataset.key);
  });
}

export function openSkillTree(skill) {
  activeSkill = skill;
  pathFor(skill);
  overlay.style.display = "flex";
  render();
}

export function closeSkillTree() {
  // The path array is the saved state -- it lives on in `paths` keyed by
  // skill, so reopening restores exactly what was allocated.
  activeSkill = null;
  overlay.style.display = "none";
}

/** Re-render if the open tree's budget changed (level-up while browsing). */
export function refreshSkillTree() {
  if (activeSkill) render();
}
