// Applies server events to client state and updates the UI log.
// No game logic here -- only state mutation and DOM updates.

import { state } from "./state.js";
import { resetMotion } from "./motion.js";

const MAX_LOG = 80;
let logEl;

// --- Packed payload decoding ---------------------------------------------
// The server ships per-tile structural fields as base64 byte arrays in
// canonical tile order (server/gep/hexgrid.py tiles_in_radius): q ascending,
// then r ascending. This must match exactly or the map shears.

function decodeBytes(b64) {
  if (!b64) return new Uint8Array(0);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function canonicalTiles(radius) {
  const tiles = [];
  for (let q = -radius; q <= radius; q++) {
    const rLo = Math.max(-radius, -q - radius);
    const rHi = Math.min(radius, -q + radius);
    for (let r = rLo; r <= rHi; r++) tiles.push([q, r]);
  }
  return tiles;
}

export function initEvents(logElement) {
  logEl = logElement;
}

function logEvent(text, cls = "") {
  if (!logEl) return;
  const div = document.createElement("div");
  div.className = `ev ${cls}`;
  div.textContent = text;
  logEl.prepend(div);
  while (logEl.children.length > MAX_LOG) logEl.removeChild(logEl.lastChild);
}

export function applySnapshot(msg) {
  state.floorNumber = msg.floor_number;
  state.radius = msg.radius;
  state.upExit = msg.up_exit;
  state.downExit = msg.down_exit;
  state.archetype = msg.archetype;
  state.safe = msg.safe;
  state.tick = msg.tick;
  state.tickDuration = msg.tick_duration;

  state.biomes = msg.biomes || {};

  // Structural payload: one byte per tile in canonical order. Rebuild the
  // same tile order the server packed against, and index it for lookups.
  state.biomeLegend = msg.biome_legend || [];
  state.biomeMap = decodeBytes(msg.biome_map);
  state.elevation = decodeBytes(msg.elevation);
  state.roughness = decodeBytes(msg.roughness);

  state.tileOrder = canonicalTiles(state.radius);
  state.tileIndex.clear();
  for (let i = 0; i < state.tileOrder.length; i++) {
    const [q, r] = state.tileOrder[i];
    state.tileIndex.set(`${q},${r}`, i);
  }

  state.roads.clear();
  for (const t of msg.roads || []) {
    state.roads.add(`${t[0]},${t[1]}`);
  }

  state.resourceNodes.clear();
  for (const [key, rid] of Object.entries(msg.resource_nodes || {})) {
    state.resourceNodes.set(key, rid);
  }

  // A snapshot is a fresh floor (or a resync): nothing should glide in from
  // wherever it happened to be standing on the previous one.
  resetMotion();

  state.monsters.clear();
  for (const [mid, m] of Object.entries(msg.monsters || {})) {
    state.monsters.set(mid, { ...m, tile: m.tile });
  }

  state.players.clear();
  for (const [pid, p] of Object.entries(msg.players || {})) {
    state.players.set(pid, { ...p, tile: p.tile });
  }

  const self = state.players.get(state.playerId);
  if (self) {
    state.selfHp = self.hp;
    state.selfMaxHp = self.max_hp;
    state.selfSkills = { ...(self.skills || {}) };
    if (self.inventory) state.selfInventory = self.inventory;
    if (self.equipment) state.selfEquipment = { ...self.equipment };
  }

  // Reset camera to centre on first snapshot
  state.cameraX = 0;
  state.cameraY = 0;

  const tag = state.safe ? " (safe town)" : "";
  logEvent(`Floor ${state.floorNumber} loaded${tag}`, "system");
}

export function applyTickResult(msg) {
  state.tick = msg.tick;
  state.tickDuration = msg.tick_duration;

  for (const ev of msg.events || []) {
    applyEvent(ev);
  }
}

function applyEvent(ev) {
  switch (ev.type) {
    case "position_update": {
      const p = state.players.get(ev.player_id);
      if (p) p.tile = ev.tile;
      break;
    }
    case "combat_result": {
      if (ev.result === "hit") {
        const m = state.monsters.get(ev.target);
        if (m) { m.hp = ev.target_hp; m.alive = ev.target_alive; }
        logEvent(`Hit for ${ev.damage.toFixed(1)} dmg`, "combat");
      } else {
        logEvent(ev.result === "dodge" ? "Dodged!" : "Missed", "combat");
      }
      break;
    }
    case "monster_moved": {
      const m = state.monsters.get(ev.monster_id);
      if (m) { m.tile = ev.tile; m.facing = ev.facing; }
      break;
    }
    case "monster_died": {
      const m = state.monsters.get(ev.monster_id);
      if (m) m.alive = false;
      logEvent(`Monster slain`, "combat");
      break;
    }
    case "monster_spawned": {
      state.monsters.set(ev.monster_id, {
        id: ev.monster_id,
        template_id: ev.template_id,
        tile: ev.tile,
        hp: 1, max_hp: 1, alive: true,
        visual: ev.visual,
        facing: ev.facing,
      });
      break;
    }
    case "item_gained":
      if (ev.player_id === state.playerId && ev.inventory) {
        state.selfInventory = ev.inventory;
      }
      logEvent(`+${ev.quantity}x ${ev.item_id}`, "gather");
      break;
    case "node_depleted":
      state.resourceNodes.delete(`${ev.tile[0]},${ev.tile[1]}`);
      break;
    case "node_respawned":
      state.resourceNodes.set(`${ev.tile[0]},${ev.tile[1]}`, ev.resource_id);
      break;
    case "gather_started":
      logEvent(`Gathering ${ev.resource_id}…`, "gather");
      break;
    case "level_up":
      logEvent(`Level up! ${ev.skill} → ${ev.new_level}`, "xp");
      break;
    case "player_update": {
      const p = state.players.get(ev.player_id) || {};
      p.hp = ev.hp;
      p.max_hp = ev.max_hp;
      p.skills = ev.skills;
      state.players.set(ev.player_id, p);
      if (ev.player_id === state.playerId) {
        state.selfHp = ev.hp;
        state.selfMaxHp = ev.max_hp;
        state.selfSkills = { ...ev.skills };
        if (ev.inventory) state.selfInventory = ev.inventory;
        if (ev.equipment) state.selfEquipment = { ...ev.equipment };
      }
      break;
    }
    case "equipment_update": {
      if (ev.player_id === state.playerId) {
        state.selfEquipment = { ...ev.equipment };
        state.selfInventory = ev.inventory;
        logEvent("Equipment changed", "system");
      }
      break;
    }
    case "move_blocked":
      logEvent("Path blocked", "system");
      break;
    case "error":
      logEvent(`! ${ev.reason}`, "system");
      break;
  }

  // Keep self stats current
  if (ev.player_id === state.playerId || ev.target === state.playerId) {
    const self = state.players.get(state.playerId);
    if (self && ev.target_hp !== undefined && ev.target === state.playerId) {
      self.hp = ev.target_hp;
      state.selfHp = ev.target_hp;
    }
  }
}
