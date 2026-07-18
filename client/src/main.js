import { state } from "./state.js";
import { initRenderer, render } from "./renderer.js";
import { initInput } from "./input.js";
import { initHud, updateHud, setIntentSender } from "./hud.js";
import { initEvents, applySnapshot, applyTickResult } from "./events.js";

// Session token from a previous login — lets the server resume us without
// re-entering credentials. Cleared if the server rejects it.
const SESSION_KEY = "mud_session";

const canvas    = document.getElementById("game");
const logEl     = document.getElementById("event-log");
const statusEl  = document.getElementById("status-bar");

const loginOverlay = document.getElementById("login-overlay");
const loginForm    = document.getElementById("login-form");
const loginError   = document.getElementById("login-error");
const loginUser    = document.getElementById("login-username");
const loginPass    = document.getElementById("login-password");
const btnLogin     = document.getElementById("btn-login");
const btnRegister  = document.getElementById("btn-register");

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
setIntentSender(send);

function showLogin(errText) {
  loginOverlay.style.display = "flex";
  loginError.textContent = errText || "";
  loginUser.focus();
}

function hideLogin() {
  loginOverlay.style.display = "none";
}

function sendAuth(type) {
  const username = loginUser.value.trim();
  const password = loginPass.value;
  if (!username || !password) {
    loginError.textContent = "Enter a username and password";
    return;
  }
  loginError.textContent = "";
  send({ type, username, password });
}

btnLogin.addEventListener("click", (e) => { e.preventDefault(); sendAuth("login"); });
btnRegister.addEventListener("click", (e) => { e.preventDefault(); sendAuth("register"); });
loginForm.addEventListener("submit", (e) => { e.preventDefault(); sendAuth("login"); });

function connect() {
  statusEl.textContent = "Connecting…";
  const token = localStorage.getItem(SESSION_KEY);
  const url = `ws://${location.hostname}:8765` + (token ? `?token=${token}` : "");
  ws = new WebSocket(url);

  ws.onopen = () => {
    statusEl.textContent = "Connected";
  };

  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    switch (msg.type) {
      case "auth_required":
        showLogin();
        break;
      case "auth_fail":
        showLogin(msg.reason);
        break;
      case "auth_ok":
        localStorage.setItem(SESSION_KEY, msg.session_token || "");
        state.playerId = msg.player_id;
        state.playerName = msg.your_name;
        statusEl.textContent = `Playing as ${msg.your_name}`;
        hideLogin();
        break;
      case "floor_snapshot":
        applySnapshot(msg);
        hideLogin();
        break;
      default:
        if (msg.tick !== undefined) applyTickResult(msg);
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
