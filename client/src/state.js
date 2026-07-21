// Single source of truth for all client-side game state.
// Nothing here computes game outcomes -- it only stores what the server sent.

export const state = {
  playerId: null,
  playerName: null,
  tick: 0,
  tickDuration: 1.0,
  floorNumber: null,
  radius: null,
  upExit: null,
  downExit: null,
  archetype: null,
  safe: false,

  // Structural payload, parallel arrays in canonical tile order
  tileOrder: [],              // [ [q,r], ... ]
  tileIndex: new Map(),       // "q,r" -> index into the arrays below
  biomeLegend: [],            // index -> biome_id
  biomeMap: new Uint8Array(0),
  elevation: new Uint8Array(0),
  roughness: new Uint8Array(0),

  // set of tile keys "q,r" that are road tiles
  roads: new Set(),
  crossings: new Set(),
  // tile key "q,r" -> reserved tile type id (server gep/tiles.py), e.g.
  // "tile_stairs_up". Sparse: only structurally significant tiles appear.
  tileTypes: new Map(),
  // biome_id -> { display_name, hsl }
  biomes: {},

  // tile key "q,r" -> resource_id
  resourceNodes: new Map(),
  // resource_id -> { display_name, category, skill }
  resources: {},
  // category id -> { display_name, skill, node_color, dot_color }
  resourceCategories: {},

  // tile key "q,r" -> prefab sprite id (town/encampment props etc.)
  prefabTiles: new Map(),

  // monster_id -> monster object
  monsters: new Map(),

  // player_id -> player object (all players on floor)
  players: new Map(),

  // Our own player's live stats (kept updated from player_update events)
  selfHp: 0,
  selfMaxHp: 1,
  selfMana: 0,
  selfMaxMana: 1,
  // Castable abilities, as sent by the server: [{ id, display_name,
  // targeting, range, aoe_radius, cooldown_ticks, mana_cost, ready_tick }].
  // Derived server-side from skills + equipment; the client only draws it.
  selfAbilities: [],
  // Transient AoE hit markers for the renderer: [{ tiles: Set<"q,r">, until }].
  // `until` is a wall-clock ms timestamp; the renderer drops expired ones.
  abilityFlashes: [],
  // skill -> { level, xp, xp_next }
  selfSkills: {},
  // 28-element array, each null or { item_id, quantity }
  selfInventory: new Array(28).fill(null),
  // { main_hand, off_hand, head, torso, hands, ... } -- see EQUIP_SLOTS in hud.js
  selfEquipment: {},

  // Camera
  cameraX: 0,
  cameraY: 0,
  zoom: 1.0,

  // Active sidebar tab: "skills" | "inventory" | "equipment"
  activeTab: "skills",
};

/** Whether terrain permits standing on a tile, per the biome definitions the
 * server sent. Entities are deliberately not considered: a monster on a tile
 * is a thing you click to attack, not a thing that makes the click invalid.
 *
 * This is not a second authority on movement -- the server rejects impassable
 * destinations regardless, and it is still the only thing that moves anyone.
 * It exists so clicking a mountain does nothing locally instead of costing a
 * round-trip to be told no. Unknown tiles and missing biome data read as
 * passable so a client that is mid-load defers to the server rather than
 * locking the player in place.
 */
export function isTilePassable(q, r) {
  const i = state.tileIndex.get(`${q},${r}`);
  if (i === undefined || i >= state.biomeMap.length) return true;
  const def = state.biomes[state.biomeLegend[state.biomeMap[i]]];
  return !def || def.passable !== false;
}
