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

  // tile key "q,r" -> resource_id
  resourceNodes: new Map(),

  // monster_id -> monster object
  monsters: new Map(),

  // player_id -> player object (all players on floor)
  players: new Map(),

  // Our own player's live stats (kept updated from events)
  selfHp: 0,
  selfMaxHp: 1,
  selfSkills: {},

  // Camera
  cameraX: 0,
  cameraY: 0,
  zoom: 1.0,
};
