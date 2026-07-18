// HUD and sidebar DOM updates. Called from the render loop.

import { state } from "./state.js";

let hpFill, hpText, tickInfo, floorLabel, skillsList;

export function initHud() {
  hpFill    = document.getElementById("hp-fill");
  hpText    = document.getElementById("hp-text");
  tickInfo  = document.getElementById("tick-info");
  floorLabel = document.getElementById("floor-label");
  skillsList = document.getElementById("skills-list");
}

export function updateHud() {
  const pct = state.selfMaxHp > 0 ? (state.selfHp / state.selfMaxHp) * 100 : 0;
  if (hpFill) hpFill.style.width = pct.toFixed(1) + "%";
  if (hpText) hpText.textContent = `${Math.ceil(state.selfHp)} / ${Math.ceil(state.selfMaxHp)}`;
  if (tickInfo) tickInfo.textContent = `tick ${state.tick}  |  ${state.tickDuration.toFixed(2)}s`;
  if (floorLabel && state.floorNumber !== null) floorLabel.textContent = `Floor ${state.floorNumber}`;

  if (skillsList) {
    const skills = state.selfSkills;
    const entries = Object.entries(skills);
    if (entries.length && skillsList.children.length !== entries.length) {
      skillsList.innerHTML = entries.map(([name, lvl]) =>
        `<div class="skill-row"><span class="skill-name">${name}</span><span class="skill-lvl">${lvl}</span></div>`
      ).join("");
    } else {
      // Update just the level values in place
      const rows = skillsList.querySelectorAll(".skill-row");
      rows.forEach((row, i) => {
        if (entries[i]) row.querySelector(".skill-lvl").textContent = entries[i][1];
      });
    }
  }
}
