import { state } from "./state.js";
import { initRenderer, render } from "./renderer.js";
import { initInput } from "./input.js";
import { initHud, updateHud } from "./hud.js";
import { initEvents, applySnapshot, applyTickResult } from "./events.js";

// Persistent identity — stored across page refreshes so the server can
// load the player's saved progress from the database.
let _token = localStorage.getItem("mud_token");
if (!_token) {
  _token = crypto.randomUUID();
  localStorage.setItem("mud_token", _token);
}
const WS_URL = `ws://${location.hostname}:8765?token=${_token}`;

const canvas    = document.getElementById("game");
const logEl     = document.getElementById("event-log");
const statusEl  = document.getElementById("status-bar") || document.getElementById("status");

initRenderer(canvas);
initHud();
initEvents(logEl);

let ws;

function send(intent) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(intent));
  }
}

initInput(canvas, send);

function connect() {
  statusEl.textContent = "Connecting…";
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    statusEl.textContent = "Connected";
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "welcome") {
      state.playerId = msg.player_id;
      state.playerName = msg.your_name;
      statusEl.textContent = `Playing as ${msg.your_name}`;
    } else if (msg.type === "floor_snapshot") {
      applySnapshot(msg);
    } else if (msg.tick !== undefined) {
      applyTickResult(msg);
    }
  };

  ws.onclose = () => {
    statusEl.textContent = "Disconnected — reconnecting in 3s…";
    setTimeout(connect, 3000);
  };

  ws.onerror = () => {
    statusEl.textContent = "Connection error";
  };
}

connect();

// Render loop — runs independent of tick rate
function loop() {
  render();
  updateHud();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
