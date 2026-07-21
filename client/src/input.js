// Translates clicks/taps on the canvas into intents sent to the server.
// Never predicts outcomes -- just sends intent and waits for server state.

import { state, isTilePassable } from "./state.js";
import { getHoveredTile } from "./renderer.js";

let sendIntent;

export function initInput(canvasEl, sendFn) {
  sendIntent = sendFn;
  canvasEl.addEventListener("click", onClick);
  canvasEl.addEventListener("touchend", onTouch, { passive: false });
  window.addEventListener("keydown", onKeyDown);
}

// Number keys 1..N cast the ability in that slot at the hovered tile. The
// server owns targeting rules and every gate (range, cooldown, mana, whether
// the player even knows it); a press it rejects just comes back as an error
// event. Ground and target_enemy abilities both aim at the tile under the
// cursor -- for a single-target ability you hover the monster, for an AoE you
// hover where you want it to land.
function onKeyDown(e) {
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return;
  const slot = Number(e.key);
  if (!Number.isInteger(slot) || slot < 1) return;
  const ability = state.selfAbilities[slot - 1];
  if (!ability) return;
  const tile = getHoveredTile();
  if (!tile) return;
  e.preventDefault();
  sendIntent({ intent_type: "use_ability", ability_id: ability.id,
               target_q: tile[0], target_r: tile[1] });
}

function tileKey(q, r) { return `${q},${r}`; }

function onClick(e) {
  const rect = e.target.getBoundingClientRect();
  handleTap(e.clientX - rect.left, e.clientY - rect.top);
}

function onTouch(e) {
  e.preventDefault();
  const touch = e.changedTouches[0];
  const rect = e.target.getBoundingClientRect();
  handleTap(touch.clientX - rect.left, touch.clientY - rect.top);
}

function handleTap(px, py) {
  const tile = getHoveredTile();
  if (!tile) return;
  const [q, r] = tile;

  // Determine what's at the tapped tile and build the right intent
  const monsterThere = [...state.monsters.values()].find(
    m => m.alive && m.tile[0] === q && m.tile[1] === r
  );
  if (monsterThere) {
    sendIntent({ intent_type: "attack", target_id: monsterThere.id });
    return;
  }

  const key = tileKey(q, r);
  const resourceThere = state.resourceNodes.get(key);
  const self = state.players.get(state.playerId);
  if (resourceThere && self && self.tile[0] === q && self.tile[1] === r) {
    sendIntent({ intent_type: "gather-node", tile_q: q, tile_r: r });
    return;
  }

  // Up/down exits: use them only while standing on the tile, otherwise walk
  // there first (a second click on the tile then takes the stairs).
  const onTile = self && self.tile[0] === q && self.tile[1] === r;
  if (state.upExit && q === state.upExit[0] && r === state.upExit[1]) {
    sendIntent(onTile ? { intent_type: "use-exit", direction: "up" }
                      : { intent_type: "move-to-tile", target_q: q, target_r: r });
    return;
  }
  if (state.downExit && q === state.downExit[0] && r === state.downExit[1]) {
    sendIntent(onTile ? { intent_type: "use-exit", direction: "down" }
                      : { intent_type: "move-to-tile", target_q: q, target_r: r });
    return;
  }

  // Default: move. Impassable terrain is dropped here rather than sent and
  // rejected -- the renderer already draws these tiles as raised barriers, so
  // a click on one is a misclick on scenery, not an action worth a round-trip.
  // The exit branches above skip this check by design: structural tiles are
  // forced back to walkable at generation, so they are never barriers.
  if (!isTilePassable(q, r)) return;
  sendIntent({ intent_type: "move-to-tile", target_q: q, target_r: r });
}
