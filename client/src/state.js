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

  // tile key "q,r" -> prefab sprite id (town/encampment props etc.)
  prefabTiles: new Map(),

  // monster_id -> monster object
  monsters: new Map(),

  // player_id -> player object (all players on floor)
  players: new Map(),

  // Our own player's live stats (kept updated from player_update events)
  selfHp: 0,
  selfMaxHp: 1,
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
