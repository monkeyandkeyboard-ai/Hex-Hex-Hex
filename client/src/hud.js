// HUD and sidebar DOM updates. Called from the render loop.
// Sidebar has three tabs: Skills (levels + XP progress), Inventory
// (28-slot grid, click to equip), Equipment (10 slots, click to unequip).

import { state } from "./state.js";

// Slot ids match the server's EQUIPMENT_SLOTS (gep/entities.py), which in
// turn match the `equipment_slot` field on every item base. One vocabulary
// end to end -- a rename here without one there shows as an empty panel.
const EQUIP_SLOTS = [
  ["head", "Head"], ["amulet", "Amulet"], ["back", "Back"],
  ["main_hand", "Main Hand"], ["torso", "Torso"], ["off_hand", "Off Hand"],
  ["hands", "Hands"], ["legs", "Legs"], ["ring", "Ring"],
  ["feet", "Feet"],
];

let hpFill, hpText, tickInfo, floorLabel;
let skillsPane, inventoryPane, equipmentPane;
let sendIntent = () => {};

// Signature strings of the last render, to skip DOM churn when unchanged
let lastSkillsSig = "", lastInvSig = "", lastEquipSig = "";

export function setIntentSender(fn) {
  sendIntent = fn;
}

export function initHud() {
  hpFill     = document.getElementById("hp-fill");
  hpText     = document.getElementById("hp-text");
  tickInfo   = document.getElementById("tick-info");
  floorLabel = document.getElementById("floor-label");
  skillsPane    = document.getElementById("tab-skills");
  inventoryPane = document.getElementById("tab-inventory");
  equipmentPane = document.getElementById("tab-equipment");

  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.addEventListener("click", () => {
      state.activeTab = btn.dataset.tab;
      for (const b of document.querySelectorAll(".tab-btn")) {
        b.classList.toggle("active", b === btn);
      }
    });
  }

  inventoryPane.addEventListener("click", (e) => {
    const cell = e.target.closest(".inv-slot");
    if (cell && cell.dataset.filled === "1") {
      sendIntent({ intent_type: "equip-item", inv_slot: Number(cell.dataset.slot) });
    }
  });

  equipmentPane.addEventListener("click", (e) => {
    const cell = e.target.closest(".equip-slot");
    if (cell && cell.dataset.filled === "1") {
      sendIntent({ intent_type: "unequip-item", equip_slot: cell.dataset.slot });
    }
  });
}

function fmt(n) {
  return Math.floor(n).toLocaleString();
}

function renderSkills() {
  const entries = Object.entries(state.selfSkills);
  const sig = JSON.stringify(entries);
  if (sig === lastSkillsSig) return;
  lastSkillsSig = sig;

  skillsPane.innerHTML = entries.map(([name, s]) => {
    // s = {level, xp, xp_next}; tolerate old plain-number form
    const level = typeof s === "object" ? s.level : s;
    const xp = typeof s === "object" ? s.xp : 0;
    const xpNext = typeof s === "object" ? s.xp_next : 0;
    const pct = xpNext > 0 ? Math.min(100, (xp / xpNext) * 100) : 0;
    return `<div class="skill-row">
      <div class="skill-head">
        <span class="skill-name">${name.replace(/_/g, " ")}</span>
        <span class="skill-lvl">${level}</span>
      </div>
      <div class="skill-xpbar"><div class="skill-xpfill" style="width:${pct.toFixed(1)}%"></div></div>
      <div class="skill-xptext">${fmt(xp)} / ${fmt(xpNext)} xp</div>
    </div>`;
  }).join("");
}

function renderInventory() {
  const inv = state.selfInventory;
  const sig = JSON.stringify(inv);
  if (sig === lastInvSig) return;
  lastInvSig = sig;

  const cells = [];
  for (let i = 0; i < 28; i++) {
    const item = inv[i];
    if (item) {
      cells.push(`<div class="inv-slot" data-slot="${i}" data-filled="1" title="${item.item_id}">
        <span class="inv-item">${item.item_id.replace(/_/g, " ")}</span>
        <span class="inv-qty">${item.quantity}</span>
      </div>`);
    } else {
      cells.push(`<div class="inv-slot empty" data-slot="${i}" data-filled="0"></div>`);
    }
  }
  inventoryPane.innerHTML = `<div class="inv-grid">${cells.join("")}</div>
    <div class="pane-hint">Click an item to equip it</div>`;
}

function renderEquipment() {
  const eq = state.selfEquipment;
  const sig = JSON.stringify(eq);
  if (sig === lastEquipSig) return;
  lastEquipSig = sig;

  equipmentPane.innerHTML = EQUIP_SLOTS.map(([slot, label]) => {
    const item = eq[slot];
    return `<div class="equip-slot ${item ? "" : "empty"}" data-slot="${slot}" data-filled="${item ? 1 : 0}">
      <span class="equip-label">${label}</span>
      <span class="equip-item">${item ? item.replace(/_/g, " ") : "—"}</span>
    </div>`;
  }).join("") + `<div class="pane-hint">Click a filled slot to unequip</div>`;
}

export function updateHud() {
  const pct = state.selfMaxHp > 0 ? (state.selfHp / state.selfMaxHp) * 100 : 0;
  if (hpFill) hpFill.style.width = pct.toFixed(1) + "%";
  if (hpText) hpText.textContent = `${Math.ceil(state.selfHp)} / ${Math.ceil(state.selfMaxHp)}`;
  if (tickInfo) tickInfo.textContent = `tick ${state.tick}  |  ${state.tickDuration.toFixed(2)}s`;
  if (floorLabel && state.floorNumber !== null) floorLabel.textContent = `Floor ${state.floorNumber}`;

  skillsPane.style.display    = state.activeTab === "skills"    ? "block" : "none";
  inventoryPane.style.display = state.activeTab === "inventory" ? "block" : "none";
  equipmentPane.style.display = state.activeTab === "equipment" ? "block" : "none";

  if (state.activeTab === "skills") renderSkills();
  else if (state.activeTab === "inventory") renderInventory();
  else renderEquipment();
}
