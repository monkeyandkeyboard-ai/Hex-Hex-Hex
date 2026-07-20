// HUD and sidebar DOM updates. Called from the render loop.
// Sidebar has three tabs: Skills (levels + XP progress), Inventory
// (28-slot grid, click to equip), Equipment (10 slots, click to unequip).

import { state } from "./state.js";
import { initSkillTree, openSkillTree, refreshSkillTree } from "./skilltree.js";

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
let skillsPane, inventoryPane, equipmentPane, tooltip;
let sendIntent = () => {};

// Signature strings of the last render, to skip DOM churn when unchanged
let lastSkillsSig = "", lastInvSig = "", lastEquipSig = "";

// Resolved item objects for whatever is currently on screen, keyed by the
// same dataset value the DOM element carries -- inv slot index or equip slot
// name. Rebuilt on every render; the hover handler just looks a key up here
// rather than re-deriving anything from the DOM.
let invItemsByKey = {};
let equipItemsByKey = {};

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
  tooltip       = document.getElementById("item-tooltip");

  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.addEventListener("click", () => {
      state.activeTab = btn.dataset.tab;
      for (const b of document.querySelectorAll(".tab-btn")) {
        b.classList.toggle("active", b === btn);
      }
    });
  }

  initSkillTree();
  skillsPane.addEventListener("click", (e) => {
    const row = e.target.closest(".skill-row");
    if (row) openSkillTree(row.dataset.skill);
  });

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

  inventoryPane.addEventListener("mouseover", (e) => {
    const cell = e.target.closest(".inv-slot");
    if (cell && cell.dataset.filled === "1") showTooltip(invItemsByKey[cell.dataset.slot]);
  });
  inventoryPane.addEventListener("mouseout", (e) => {
    if (e.target.closest(".inv-slot")) hideTooltip();
  });
  inventoryPane.addEventListener("mousemove", moveTooltip);

  equipmentPane.addEventListener("mouseover", (e) => {
    const cell = e.target.closest(".equip-slot");
    if (cell && cell.dataset.filled === "1") showTooltip(equipItemsByKey[cell.dataset.slot]);
  });
  equipmentPane.addEventListener("mouseout", (e) => {
    if (e.target.closest(".equip-slot")) hideTooltip();
  });
  equipmentPane.addEventListener("mousemove", moveTooltip);
}

function moveTooltip(e) {
  if (tooltip.style.display !== "block") return;
  const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const maxX = window.innerWidth - tooltip.offsetWidth - 8;
  const maxY = window.innerHeight - tooltip.offsetHeight - 8;
  if (x > maxX) x = e.clientX - tooltip.offsetWidth - pad;
  if (y > maxY) y = Math.max(8, maxY);
  tooltip.style.left = x + "px";
  tooltip.style.top = y + "px";
}

function hideTooltip() {
  tooltip.style.display = "none";
}

const STAT_LABELS = {
  strength: "Strength", dexterity: "Dexterity", precision: "Precision",
  arcana: "Arcana", mana_attunement: "Mana Attunement", constitution: "Constitution",
  critical_strike_chance: "Crit Chance",
};

function statLabel(stat) {
  if (stat.endsWith("_percent")) {
    const root = stat.slice(0, -"_percent".length);
    return (STAT_LABELS[root] || root.replace(/_/g, " ")) + " %";
  }
  return STAT_LABELS[stat] || stat.replace(/_/g, " ");
}

function pct(n) {
  return `${Math.round(n * 100)}%`;
}

function showTooltip(item) {
  if (!item) return;

  // Materials only carry an item_id + display_name -- nothing else to show.
  if (item.type === undefined) {
    tooltip.innerHTML = `<div class="tt-name">${item.display_name}</div>`;
    tooltip.style.display = "block";
    return;
  }

  const rows = [];
  rows.push(`<div class="tt-row"><span class="k">Type</span><span class="v">${item.type}</span></div>`);
  if (item.damage_max > 0) {
    rows.push(`<div class="tt-row"><span class="k">Damage</span><span class="v">${pct(item.damage_min)}–${pct(item.damage_max)} of power</span></div>`);
    rows.push(`<div class="tt-row"><span class="k">Speed</span><span class="v">${item.speed_ticks} ticks</span></div>`);
  }
  if (item.armor > 0) {
    rows.push(`<div class="tt-row"><span class="k">Armor</span><span class="v">${item.armor}</span></div>`);
  }

  const statRows = Object.entries(item.stats || {}).map(([stat, value]) => {
    const shown = stat.endsWith("_percent") ? pct(value) : `+${value}`;
    return `<div class="tt-row"><span class="k">${statLabel(stat)}</span><span class="v">${shown}</span></div>`;
  });

  const modRows = (item.mods || []).map((m) => {
    const cls = m.affix === "P" ? "prefix" : "suffix";
    const shown = m.stat.endsWith("_percent") ? pct(m.value) : `+${m.value}`;
    return `<div class="tt-mod ${cls}">${m.affix === "P" ? "Prefix" : "Suffix"}: ${statLabel(m.stat)} ${shown} (T${m.tier})</div>`;
  });

  tooltip.innerHTML = `
    <div class="tt-name">${item.display_name}</div>
    <div class="tt-sub">Tier ${item.tier} — ${item.equipment_slot.replace(/_/g, " ")}</div>
    ${rows.join("")}
    ${statRows.length ? `<div class="tt-divider"></div>${statRows.join("")}` : ""}
    ${modRows.length ? `<div class="tt-divider"></div>${modRows.join("")}` : ""}
  `;
  tooltip.style.display = "block";
}

function fmt(n) {
  return Math.floor(n).toLocaleString();
}

function renderSkills() {
  const entries = Object.entries(state.selfSkills);
  const sig = JSON.stringify(entries);
  if (sig === lastSkillsSig) return;
  lastSkillsSig = sig;
  // A level-up changes the tree's point budget, so an open tree must redraw.
  refreshSkillTree();

  skillsPane.innerHTML = entries.map(([name, s]) => {
    // s = {level, xp, xp_next}; tolerate old plain-number form
    const level = typeof s === "object" ? s.level : s;
    const xp = typeof s === "object" ? s.xp : 0;
    const xpNext = typeof s === "object" ? s.xp_next : 0;
    const pct = xpNext > 0 ? Math.min(100, (xp / xpNext) * 100) : 0;
    return `<div class="skill-row" data-skill="${name}">
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

  invItemsByKey = {};
  const cells = [];
  for (let i = 0; i < 28; i++) {
    const slot = inv[i];
    if (slot) {
      invItemsByKey[i] = slot.item;
      cells.push(`<div class="inv-slot" data-slot="${i}" data-filled="1">
        <span class="inv-item">${slot.item.display_name}</span>
        <span class="inv-qty">${slot.quantity}</span>
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

  equipItemsByKey = {};
  equipmentPane.innerHTML = EQUIP_SLOTS.map(([slot, label]) => {
    const item = eq[slot];
    if (item) equipItemsByKey[slot] = item;
    return `<div class="equip-slot ${item ? "" : "empty"}" data-slot="${slot}" data-filled="${item ? 1 : 0}">
      <span class="equip-label">${label}</span>
      <span class="equip-item">${item ? item.display_name : "—"}</span>
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
