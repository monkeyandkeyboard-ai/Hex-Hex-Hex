// Applies server events to client state and updates the UI log.
// No game logic here -- only state mutation and DOM updates.

import { state } from "./state.js";
import { resetMotion } from "./motion.js";
import { resetColorCache, resetGeometry } from "./renderer.js";

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

  // Reserved tile types (server gep/tiles.py): sparse "q,r" -> type id.
  state.tileTypes.clear();
  for (const [key, tid] of Object.entries(msg.tile_types || {})) {
    state.tileTypes.set(key, tid);
  }

  state.roads.clear();
  for (const t of msg.roads || []) {
    state.roads.add(`${t[0]},${t[1]}`);
  }

  // Barrier tiles the generator opened so the exits stay reachable
  // (server gep/passability.py). These arrive as coordinates rather than
  // being derivable from biome data: carving rewrites a ford's biome to
  // walkable ground, so by the time the client sees it, nothing in the
  // terrain says a crossing was ever cut there.
  state.crossings.clear();
  for (const t of msg.crossings || []) {
    state.crossings.add(`${t[0]},${t[1]}`);
  }

  state.resources = msg.resources || {};
  state.resourceCategories = msg.resource_categories || {};

  state.resourceNodes.clear();
  for (const [key, rid] of Object.entries(msg.resource_nodes || {})) {
    state.resourceNodes.set(key, rid);
  }

  state.prefabTiles.clear();
  for (const prefab of msg.prefabs || []) {
    for (const [key, sprite] of Object.entries(prefab.tile_sprites || {})) {
      state.prefabTiles.set(key, sprite);
    }
  }

  // A snapshot is a fresh floor (or a resync): nothing should glide in from
  // wherever it happened to be standing on the previous one, and the cached
  // per-tile geometry describes the old floor.
  resetMotion();
  resetGeometry();
  // Colours and textures are cached by biome *index*, and the biome legend is
  // per-floor -- index 2 is not the same biome on the next floor down. Without
  // this the new floor paints in the old floor's palette.
  resetColorCache();

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
    if (self.mana !== undefined) state.selfMana = self.mana;
    if (self.max_mana !== undefined) state.selfMaxMana = self.max_mana;
    if (self.abilities) state.selfAbilities = self.abilities;
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
      if (p) { p.tile = ev.tile; if (ev.facing) p.facing = ev.facing; }
      break;
    }
    case "combat_result": {
      // Monsters attack now, so the player is not always the attacker.
      // Reporting an incoming hit as "Hit for 330" reads as though you dealt
      // it, which is exactly backwards at the moment it matters most.
      const incoming = ev.target === state.playerId;
      if (ev.result === "hit") {
        const m = state.monsters.get(ev.target);
        if (m) { m.hp = ev.target_hp; m.alive = ev.target_alive; }
        if (incoming) {
          state.selfHp = ev.target_hp;
          logEvent(`Took ${ev.damage.toFixed(1)} dmg`, "combat");
        } else {
          logEvent(`Hit for ${ev.damage.toFixed(1)} dmg`, "combat");
        }
      } else if (incoming) {
        logEvent(ev.result === "dodge" ? "You dodged!" : "Attack missed you", "combat");
      } else {
        logEvent(ev.result === "dodge" ? "Dodged!" : "Missed", "combat");
      }
      break;
    }
    case "player_died":
      if (ev.player_id === state.playerId) {
        state.selfHp = ev.hp;
        logEvent(`You died — returned to your anchor`, "combat");
      }
      break;
    case "engagement_started":
      if (ev.player_id === state.playerId) logEvent("Engaged", "combat");
      break;
    case "engagement_ended":
      if (ev.player_id === state.playerId) logEvent(`Disengaged (${ev.reason})`, "combat");
      break;
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
        display_name: ev.display_name,
        level: ev.level,
        tile: ev.tile,
        hp: ev.hp, max_hp: ev.max_hp, alive: true,
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
    case "item_dropped_inventory_full":
      // The drop was rolled and lost. Tell the player -- silently voiding
      // loot is worse than not rolling it.
      logEvent(`Inventory full — lost ${ev.item_id}`, "combat");
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
        if (ev.mana !== undefined) state.selfMana = ev.mana;
        if (ev.max_mana !== undefined) state.selfMaxMana = ev.max_mana;
        if (ev.abilities) state.selfAbilities = ev.abilities;
        state.selfSkills = { ...ev.skills };
        if (ev.inventory) state.selfInventory = ev.inventory;
        if (ev.equipment) state.selfEquipment = { ...ev.equipment };
      }
      break;
    }
    case "ability_used": {
      // Cosmetic only: flash the impacted hexes so an AoE reads as an area,
      // not a single hit. The server has already resolved every result; the
      // combat_result events that ride alongside carry the actual damage.
      state.abilityFlashes.push({
        q: ev.tile[0], r: ev.tile[1],
        radius: ev.aoe_radius || 0,
        until: performance.now() + 350,
      });
      if (ev.caster === state.playerId) {
        const name = (state.selfAbilities.find(a => a.id === ev.ability_id) || {}).display_name;
        logEvent(`Cast ${name || ev.ability_id}`, "combat");
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
