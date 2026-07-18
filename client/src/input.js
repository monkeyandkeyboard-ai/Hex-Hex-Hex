// Translates clicks/taps on the canvas into intents sent to the server.
// Never predicts outcomes -- just sends intent and waits for server state.

import { state } from "./state.js";
import { getHoveredTile } from "./renderer.js";

let sendIntent;

export function initInput(canvasEl, sendFn) {
  sendIntent = sendFn;
  canvasEl.addEventListener("click", onClick);
  canvasEl.addEventListener("touchend", onTouch, { passive: false });
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

  // Default: move
  sendIntent({ intent_type: "move-to-tile", target_q: q, target_r: r });
}
