# HEX TOWER MUD — Complete Design & Architecture Compendium

**Version:** 1.0 (compiled from full design sessions)
**Audience:** a coding agent (Claude Code or similar) with NO access to prior conversations or the original design documents. This document is deliberately self-contained: every formula, rule, and decision needed to build the game is reproduced here, not referenced externally.
**Status legend used throughout:** `[CONFIRMED]` = explicitly decided by the project owner. `[CANON]` = carried forward from the owner's earlier design documents, reproduced here in full. `[OPEN]` = deliberately undecided; do NOT guess — ask or defer. `[INFERRED]` = this document's reasonable assumption, flagged for the owner to veto.

---

# PART I — VISION AND IDENTITY

## 1. The Game in One Paragraph

A browser-playable, tick-based MMO set in one or more procedurally generated, effectively infinite **towers**. Each floor of a tower is a bounded hexagonal arena. Players all begin at the bottom, climb at their own pace, fight monsters, gather resources, craft procedurally rolled equipment, and trade through a multi-tier economy. Difficulty and reward scale with floor height. The game is solo-viable forever; groups multiply efficiency but never gate content. The fun comes from systems and mechanics, not graphics. It must be cheap to run at scale, playable on any device with a browser, and administrable long-term by one person editing config files.

## 2. Design Identity — the principles that repeat everywhere `[CONFIRMED]`

These are not decorative values. They were arrived at independently in five or six different subsystems, which is why they are canon identity. When designing anything new, check it against these:

1. **Reward perception, not memorization.** The skill ceiling comes from noticing legible, in-world mechanics (tick timing, rare-spawn priority, travel-route optimization) — never from hidden formulas or wiki-required knowledge. A player who pays attention outperforms one who doesn't; the inattentive player is never punished beyond not receiving the edge.
2. **The mechanism is visible, never hidden.** Tick rate is shown to players. Server slowdown (tick dilation) is shown as a number, not disguised. Rare monsters are visually identifiable before engagement. The game never papers over its own machinery.
3. **Simple rules → emergent depth.** Population caps + respawn rolls produce kill-order strategy without any dedicated code. Unequal tower-intersection periods produce a rarity hierarchy of junction floors from pure arithmetic. Prefer one general rule over ten special cases, at both the design layer and the code layer.
4. **The player always has another lever.** Item quality = luck (material roll) × skill (execution timing) × investment (post-craft spending) — three independent axes, so no single bad outcome is a dead end.
5. **Solo-viable, group-efficient.** Group content makes hard things faster, never makes them possible-at-all. `[CONFIRMED]` verbatim intent: "this game should be able to be played solo while group content is just maximizing efficiency on difficult content."
6. **Data over vibes for balance.** Every balance decision should eventually be checkable against collected aggregate data (Part VII). Every tunable number lives in config, never in code.

## 3. Owner's Background & Collaboration Model `[CONFIRMED]`

The project owner is a veteran MMO player (RuneScape Classic → RS2 rares trading → quit at EoC; top-level OSRS PvP/PvE; heavy WoW trading including multiple Spectral Tiger accounts). Treat their design instincts about game feel, economies of rare items, and tick-based combat as expert input, not something to re-explain.

**Division of labor, explicitly agreed:**
- **Owner:** defines goals, refines explanations, playtests, and tunes config values directly ("go into `xyz.cfg`, tweak `abc_rate`"). Can read and reason about individual lines of code locally, but does NOT maintain global code cohesion and does not want to.
- **Coding agent:** owns ALL code and its cohesion — ensuring a config-only change never breaks anything, ensuring new content follows the data-driven schema rather than special-casing, keeping every architectural rule in this document true as the codebase grows. Do not hand architecture-cohesion decisions back to the owner for review; that responsibility was explicitly delegated.
- The owner explicitly does NOT want: to code from the ground up, to mentally hold the whole structure, or to have engine-level responsibilities. They DO want: to patch things together, tweak values, and add content via config.

---

# PART II — WORLD STRUCTURE

## 4. The Tower Model `[CONFIRMED — this replaced an earlier open-world hex-plane design]`

The world is one or more named vertical **towers** (working names: Tower A, B, C…). This replaced an earlier infinite open hex-plane design for a specific stated reason: the owner hates that open worlds force map-reading ("I would rather players be able to get where they want to go intuitively rather than having to spend all of their gaming time figuring out where their friends are"). Floor numbers are self-describing addresses: "meet me at floor 875" is a complete instruction requiring no map.

### 4.1 Floors
- A floor is a **bounded hexagonal disc** of tiles. `[CONFIRMED]` minimum radius 64 ("smaller than 64 will feel claustrophobic"); larger radii (128, 256) possible per floor type. Tile count for radius r = `3r² + 3r + 1` (r=64 → 12,481 tiles; r=128 → 49,537; r=256 → 197,377).
- Every floor is **procedurally generated, deterministically, from its inputs** (tower ID, floor number, seed, generation ruleset). `[CONFIRMED]`
- Every floor always contains **a way up and a way down** — except floor 1, which has only a way up ("unless I later add a way down" — leave the hook, don't build the down-exit on floor 1). `[CONFIRMED]`
- The guaranteed down-exit doubles as a **self-directed de-escalation valve**: a player in over their head can always retreat under their own power without dying. This partially resolves the death-penalty/travel-time tension in §14.
- Floors have a **deterministic probability of generating features** (resource nodes, monster spawns, structures) based on generation inputs. `[CONFIRMED]`
- **Within a floor, movement and play are hex-tile-based** — tile occupation, discrete movement, the owner's stated love of hexagonal games lives at this scale.

### 4.2 Tower intersections `[CONFIRMED]`
Towers intersect at floors determined by simple periodic rules — owner's example: A↔B every 25 floors, A↔C every 50, B↔C every 33. Because the periods are unequal, junction rarity emerges from arithmetic: A-B junctions are common; A-B-C triple junctions occur only at the LCM of all periods (for 25/50/33 that is floor **1,650** — note: this was miscalculated as 8,250 once in conversation and corrected; 1,650 is right: 2×3×5²×11). Junction floors are structurally scarce and are therefore the natural home of contested content: faction territory contests (§17), trade hubs (§16.4), and admin-tuned "optimal maps" (§12).

### 4.3 Vertical scaling of danger and reward `[CONFIRMED]`
Difficulty and reward both increase with floor height. This is the tower-model successor to the earlier "distance from spawn" scaling, and it is explicitly a **market-force tuning lever**, not just a difficulty curve — the admin can tune the steepness to shape where population concentrates, and to make high-floor trade listings worth their travel/shipping costs. `[OPEN]` The exact curve (per-floor scaling formula) is undefined; make it config-driven from day one.

### 4.4 "Infinite" `[PARTIALLY OPEN]`
Floors cost nothing until first visited (deterministic generation + dormancy, §7), so **unbounded floor count per tower is effectively free** — floor 50,000 generates on demand exactly like floor 10. `[OPEN]` Whether the set of named towers is fixed (A/B/C forever) or grows over time as population grows is genuinely undecided. Do not assume either.

## 5. Travel & Navigation `[CONFIRMED]`

- **Free option, always:** physically running through floors (enter, cross to the exit, ascend/descend). This is what most players do most of the time, and it doubles as the resource-gathering/environment-interaction loop — travel and play are the same activity.
- **Free floor-skip waypoints** exist at fixed points ("free 'floor skip' or optimal route" — exact placement `[OPEN]`, but they exist by design).
- **Paid fast travel** between floors, priced steeply enough that cost is a real decision: a player who earns on high floors can afford routine jumps, but floor 1 → floor 1650 should cost "a significant portion of their money." `[CONFIRMED]` verbatim intent.
- **Route optimization is intended skill expression:** the economical play is fast-travel to the nearest free waypoint, then run the cheap remainder. "Similar tricks to save cost will allow a crafty trader to profit more by optimizing their cost logistics." This is Design Identity #1 (reward perception) applied to logistics — build the pricing so that clever routing genuinely beats naive point-to-point payment.
- Travel pricing must be config (§19).

---

# PART III — SIMULATION CORE

## 6. The Tick Loop `[CANON, extended by CONFIRMED decisions]`

- The authoritative server process is the **GEP (Game Engine Process)**. Base tick rate: **1 Hz** (one tick = one second at standard speed).
- Per tick: (1) drain inbound player-intent queue → (2) advance tick counter → (3) execute every queued action whose `Target_Tick` == now → (4) update in-memory live state → (5) broadcast results to connected clients.
- **Time-spanning actions** (travel, mining, crafting, weapon cooldown) are queue entries with a precomputed `Target_Tick`. They are never re-simulated per tick — only checked for arrival. Combat cooldowns, resource respawns, and spawn timers all use this one mechanism.
- **The tick is the game-logic unit players learn to feel.** All timing windows, cooldowns, and precision mechanics are expressed in ticks, never in wall-clock seconds.

### 6.1 Tick dilation `[CONFIRMED — primary load-management strategy]`
- The tick as a **unit of game logic** is fixed; the tick as a **unit of wall-clock time** is allowed to stretch under load. When a floor's population makes its tick processing approach the real-time budget, the floor's tick duration expands (e.g., 1.0s → 1.4s) rather than the CPU redlining or the simulation degrading. Owner's framing, verbatim: "Instead of redlining the cpu to complete everything within the tick window, just expand the window."
- **Dilation is per-floor and strictly uniform:** every entity on a dilated floor — every player, monster, action queue — slows by the same factor simultaneously. Relative timing between players on the same floor NEVER shifts. This is a hard correctness constraint, not a preference: uneven dilation would corrupt the fairness of every timing mechanic in §8.
- **The current tick duration is broadcast to clients and shown to players as a plain number.** `[CONFIRMED]` This is Design Identity #2: the mechanism is visible. The playerbase this game targets reads tick rates natively; show the literal duration, not a euphemism. A dilated floor is neutral shared information ("this floor is busy"), potentially even a draw.
- Client animations/tweens scale trivially: `duration = base_duration × (current_tick_length / standard_tick_length)`. The dilation factor must be in the broadcast state anyway for tween correctness, so displaying it costs nothing.
- This is modeled on EVE Online's time-dilation approach to overloaded star systems — a proven pattern in the closest real-world analogue this game has.

## 7. Floor Lifecycle: Active, Dormant, Catch-Up `[CONFIRMED]`

- A floor is **active** only while at least one player is on it. Active floors live in a GEP worker's memory and tick normally.
- When the last player leaves, the floor goes **dormant**: its state diff is persisted (§10) and it is unloaded from memory. Dormant floors cost storage only — zero compute, zero GEP memory. "Potentially hundreds of floors could be active" concurrently while thousands more lie dormant; cost tracks *populated* floors, not total floors.
- **Catch-up on re-entry:** dormant floors are not simulated. Time-driven state changes (resource respawn, monster population regen) store a timestamp of last change; when a player enters, elapsed time is compared against respawn rates and the result computed algebraically in one step. Owner's framing: "If there is a state change that occurs after a period of time we can log that time and check if there needs to be a respawn after a player enters."
- **Correctness requirement:** the catch-up formula must produce *exactly* what continuous ticking would have produced — any asymmetry between staying-and-watching and leaving-and-returning is an exploit (or an accidental punishment) that players will find. Note this self-detects: it appears as a configured-vs-observed gather-rate gap in the analytics (§22).
- Events requiring player presence (kills, contests, PvP) need no catch-up logic by definition — they cannot occur on an empty floor.

## 8. Timing-Quality: the Precision Grade primitive `[CONFIRMED]`

One generic mechanism, built once, opt-in per action via config — never reimplemented per skill:

- Any queued action MAY declare a **precision window**: a target tick (or narrow range) at which a follow-up player input is evaluated.
- The GEP compares input-arrival tick vs. ideal tick and produces a **Precision Grade** (e.g., Poor / Normal / Good / Perfect) — one scalar, computed by one shared function.
- Each system interprets the grade independently:
  - **Combat** → temporary stat/damage bonus on that action.
  - **Gathering** → yield quantity and/or material-quality bonus.
  - **Crafting** → output-quality bonus (feeds §13's quality levers).
- `[CONFIRMED]` default: timing is **upside-only** — a missed window forfeits the bonus but never produces a worse-than-baseline outcome. (Owner confirmed rewards like "additional stats for that action or a higher quality outcome while crafting, or a bigger yield/better quality yield." Whether any action should ever actively punish a miss is `[OPEN]` pending playtesting.)
- The intended feel, owner's words: "intuitive, rhythmic without being a music game, and strategic." The tick gives a pulse to entrain to; the payoff compounds across systems (a well-timed gather feeds a well-timed craft feeds a well-timed fight).
- OSRS is the explicit reference point for how tick mechanics create skill depth. The owner is an expert OSRS player; the design intends the same property — the tick is legible raw material for mastery, not hidden latency.

## 9. Server Topology & Scaling `[CONFIRMED direction, revised from the original zone-sharding plan]`

**Language: Python, kept deliberately.** `[CONFIRMED after direct discussion]` Rationale, preserved so it isn't relitigated from scratch: (a) EVE Online — the closest architectural cousin this game has: persistent world, tick simulation, real player economy — has run its entire backend on (Stackless) Python since the late 1990s at 20k+ concurrent users on a single logical shard, so "Python can't run an MMO server" is empirically false; (b) the per-tick work here is small arithmetic and table lookups, not physics — Python's raw speed is not the binding constraint at realistic populations; (c) real working domain logic already exists in Python. Known constraint: the GIL caps one Python process at ~one CPU core. EVE's answer — many single-core processes, one per world-region — is the same worker model below. If profiling ever shows raw per-tick arithmetic as the true bottleneck, the targeted fix is rewriting the hot inner loop only (called from Python), not a wholesale language switch.

**Topology: a worker pool + directory, not fixed geographic shards.** Because floors are self-contained, cheaply portable units that exist only while populated (§7), the right model is:
- A pool of GEP worker processes. Each worker hosts **as many concurrently-active floors as fit its budget** — not one floor per process.
- A lightweight **directory** (natural fit: Redis) mapping active floor → owning worker. A floor is claimed by an available worker when a player arrives and released to dormant storage when the last player leaves.
- Workers scale horizontally by adding processes/machines as total active-floor load grows.

**Load-response escalation ladder, in order:**
1. **Tick dilation (§6.1)** — the default, near-free response to a floor getting busy. Handles the common case entirely.
2. **Worker rebalancing** — moving *dormant-at-the-moment* or lightly-loaded floors between workers is cheap and safe.
3. **Dedicated promotion** — a persistently overloaded floor gets its own worker/machine (the "Jita pattern," after EVE's dedicated-blade trade hub). Floors must be built as self-contained handoff-able units from day one so this is *possible* — but the live-migration mechanism itself (moving a floor between workers while players are mid-action on it) is **deliberately deferred until real measured load demands it**. Do not build it speculatively.
4. **Intra-floor spatial partitioning** (splitting one floor's tiles across workers, syncing only at region borders) — a known escalation if a single floor outgrows even a dedicated worker + dilation. Documented as the eventual answer; not to be built until needed.

**Explicitly rejected:** building the original design's full cross-shard handover protocol and fixed zone-sharding on day one. First build: one worker, one active floor set, whole game in one process. Add workers when measured tick-processing time approaches budget — a number to watch in production, not to guess at now.

**Reliability note (distinct from scaling):** process crash recovery = restart worker, reload active floors' last-persisted diffs. Sharding does not provide reliability; small blast radii and clean restart-from-persistence do.

## 10. Data Layers `[CANON + CONFIRMED]`

One-directional data flow; nothing reaches backward:

1. **Static config (JSON, read-only at runtime):** all content definitions and all tunable numbers (Part V, §19). Loaded at worker startup. The rulebook.
2. **Live volatile state (Redis):** active-floor entity state, player position/HP/mana/buffs, action queues, order books, the floor→worker directory. UUIDv7 is the global ID standard for all entities. `[CANON]`
3. **GEP workers:** the only layer that computes game outcomes. Reads 1, holds 2, publishes to 4, batch-writes to 5.
4. **Event bus (Kafka or RabbitMQ):** cross-floor/cross-worker events, plus the analytics subscription feed. Publishers never wait on subscribers; analytics can never slow the tick.
5. **Durable persistence (Postgres):** accounts, inventory ownership, completed trades, guild/faction membership, dormant-floor state diffs. Batched writes, never per-tick, never in the hot path.
6. **Analytics aggregator (Part VII):** consumes the bus, writes only de-identified aggregates. Structurally cannot write player identity downstream (§23).
7. **Client (Part IV):** talks to workers over WebSocket only; touches nothing else.

### 10.1 Floor state as diff-from-deterministic-baseline `[CONFIRMED]`
Because generation is deterministic, a floor's stored/transmitted state is only its **deviation from the freshly-generated baseline**: depleted nodes, dead monsters, player-made changes. An untouched floor stores and transmits nothing. Bandwidth and storage cost scale with *player impact*, not floor size or floor count. Napkin math kept for calibration: even the naive full-state worst case at radius 64 (~12.5k tiles × ~16 bytes) is ~200 KB one-time on floor entry — trivially small; the diff approach makes the realistic number far smaller.

---

# PART IV — CLIENT

## 11. Client Architecture `[CONFIRMED — no game engine]`

**A plain web app. No Godot, no Unity.** Decision rationale preserved: the client's whole job is a hex renderer plus ordinary UI (inventory, skills, trade, chat) — a web-app problem; "any device, no install" is best served by the browser natively; client and server iterate in parallel over a plain WebSocket/JSON contract with no export toolchain; and a coding agent is far more fluent in JS/React/Canvas than in engine scripting, which matters because tooling-fights are what killed this project's previous attempts. An engine would earn its keep only for real 3D/particles/app-store installs — none apply.

**The client contract (inviolable):**
- Client sends **intent** (`move-to-tile`, `attack-target`, `gather-node`, `craft-item`, …) and renders **authoritative state** broadcast by the server.
- Client NEVER predicts outcomes: no combat results, no yields, no drops, nothing another player's view depends on. Cosmetic-only interpolation is allowed and encouraged: tweening an entity between two known-true tile positions, sprite facing, etc. The line, verbatim from design discussion: predict only things that are at worst *cosmetically* wrong for a fraction of a second — never things that are *factually* wrong.
- Discrete tile occupation means there is no smoothness problem for prediction to solve — one tick of latency on a discrete-state game is invisible. This deletes the entire client-reconciliation bug class by design. OSRS is the reference: read state, play the matching animation, minimal correction; "good players know which tile they are actually on. It isn't seen as a hindrance, just a mechanic to perfect." `[CONFIRMED]`
- Wire shape `[INFERRED, refine at implementation]`: client → `{intent_type, target, params}`; server → `{tick, tick_duration, entity_updates[], event_log[]}`.

### 11.1 Client-side deterministic generation `[CONFIRMED]`
The client runs the SAME floor-generation function as the server, from the same inputs, and receives only the state diff (§10.1). Owner's words: "sync the client side floor map so I don't have to transmit so much data, just states for each tile."
**Hard requirement:** generation must be bit-for-bit reproducible across Python (server) and JS (client) — use one explicitly-specified seeded PRNG implemented identically on both sides; avoid floating-point-sensitive branching in generation logic. "Same algorithm" is not automatically "same output" across languages; this must be engineered deliberately and covered by cross-language golden-output tests.

## 12. Rendering & Visual Assets

- **Rendering depth ladder** (each step is presentation-only; none changes the contract): CSS-shaded tiles → Canvas2D with per-tile lighting → thin WebGL layer for real shader effects. Start at the simplest step that looks acceptable; upgrade later without rearchitecting. 2D now, possible 2.5D later. `[CONFIRMED]`
- **Parameterized shaders for single-silhouette content** (ore, trees, terrain features): reusable GLSL "stamps" whose configurable uniforms (seed, color, size, pattern density) produce visually distinct instances from one shader. **Wire quality/grade (§13) into these uniforms** so a high-grade node visibly differs from a low-grade one — appearance driven by a number the server already computes. (Working GLSL of this kind existed in the prior codebase — `ore.glsl`, `trees.glsl`, etc. with documented CONFIGURABLE INPUTS blocks; the pattern is proven even if the files aren't available.)
- **Composited parts library for creatures** `[CONFIRMED — explicitly chosen OVER full procedural skeletons]`: creature visual variety comes from pre-made 2D parts (arms, legs, torsos, heads, facial features down to eye color) assembled onto named skeleton slots. The owner explicitly rejected procedurally-generated per-part shapes after recognizing it would mean "an infinite cycle of fixing constraints to prevent visual glitches" — pre-validated parts that are built to layer are a bounded problem; novel generated shapes that must never clip/glitch in any combination are not. Parts are selected by **weighted per-slot roll in the monster's config** — the same weighted-roll machinery as loot tables and stat variance, pointed at appearance.
- **Rare parts carry mechanical hooks via config, never engine special-cases:** a rare eye color's own config can say "on spawn, apply modifier X" (referencing the verb vocabulary, §20) — a legible pre-fight signal to attentive players AND a reward/difficulty modifier. Owner: "a specific eye color could be rare and trigger a special reward or difficulty for the enemy… Basically I want a bunch of 'body parts' 'plant parts' 'mineral parts' and textures to apply to them." `[CONFIRMED]`
- **Player characters use the same parts system, player-selected instead of rolled** `[CONFIRMED]`: "Customizable characters, will have premade features. Legs, torso, head, arms, and facial features in a clip art esque fashion. It's 2d potentially a 2.5d upgrade later." One slot/anchor contract serves both creature variety and player self-expression; community-contributed parts conform to the same contract.
- **Production note:** the real cost is stylistic consistency across the parts library (proportions, anchor points, lighting) so any arm fits any torso — a pipeline problem (style guide, reference sheets, consistent generation prompts), not an architecture problem. Mass-generated AI art curated into a consistent library is the owner's stated intent.
- **Accessibility requirement, non-negotiable** `[CONFIRMED]`: no rarity or important-state signal may rely on color alone — always pair a redundant non-color cue (shape, icon, pattern, animation). Roughly 8% of men have red-green color deficiency; a color-only rare signal structurally excludes them, which directly violates Design Identity #1 (reward attention, never exclude by trait). §22 describes how to verify this against live behavioral data.

---

# PART V — GAME SYSTEMS

## 13. Skills, Combat, Items `[CANON — reproduced in full since source docs are unavailable]`

### 13.1 Skills
**Six combat skills:** Precision, Strength, Dexterity, Arcana, Mana Attunement, Constitution.
**Eight non-combat skills:** Mineralogy, Smithing, Foraging, Farming, Alchemy, Enchanting, Aboriculture, Craftsmanship.
Each non-combat skill is influenced by a weighted subset of combat skills. Non-combat actions award XP to the primary (non-combat) skill AND a weighted share to its influencing combat skills. All XP rates live in an `xp_rates.json`-style config. Combat XP is granular: damage-based XP to Strength/Arcana/Precision per the damage dealt and type; defensive XP to Constitution from damage taken/mitigated; resource-usage XP to Mana Attunement/Dexterity. XP awards and level-ups are atomic updates within the tick.

### 13.2 Combat resolution — the 6-step atomic flow
Executes atomically within one tick upon a successful weapon-ready check, using current in-memory live stats of Attacker (A) and Target (T). All constants come from a `combat_scaling_constants.json`-style config file — never hardcoded.

1. **Skill outputs (pre-fight):**
   - `T_Evasion_Rating = Dexterity_T × Evasion_Multiplier`
   - `A_Hit_Chance_Base = min(0.95, 0.5 + Precision_A / (Precision_A + Constant_C))`
   - `A_Damage_Multiplier = 1 + Strength_A / Constant_Divisor`
   - `A_Potency = 1 + (Arcana_A)^1.1 / Constant_Divisor`
   - `T_Raw_Mitigation = Constitution_T × Mitigation_Multiplier`
2. **Evasion check:** `Evasion_Chance = T_Evasion_Rating / (T_Evasion_Rating + Precision_A)`. Roll R1 ∈ [0,1]; if R1 < Evasion_Chance → DODGE, flow ends, broadcast.
3. **Hit check:** `Hit_Chance = A_Hit_Chance_Base`. Roll R2; if R2 ≥ Hit_Chance → MISS, flow ends, broadcast. (Design note in canon: opponent-level factor may be added later.)
4. **Base damage (on hit):** `Base_Damage = Weapon_Base_Damage × Damage_Type_Multiplier`, where the multiplier is `A_Damage_Multiplier` for melee/ranged or `A_Potency` for arcana. Damage type carries into step 5.
5. **Mitigation:** `Effective_Mitigation = T_Raw_Mitigation × Damage_Type_Weighting` (canon weights: Physical 1.0, Arcana 0.5, Elemental 0.75 — config values). Soft-cap conversion so 100% reduction is unreachable: `Mitigation_% = Effective_Mitigation / (Effective_Mitigation + Defense_Soft_Cap_Factor)`.
6. **Final:** `Final_Damage = Base_Damage × (1 − Mitigation_%)`. Apply to T's HP in memory, broadcast a combat-log event with final damage and new HP, queue the attacker's `WEAPON_COOLDOWN` at its `Target_Tick`.

Precision Grade (§8) bonuses apply as temporary modifiers into this flow (e.g., to damage or hit chance for that action) — exact insertion points are config-defined per ability.

### 13.3 Entity stats & monsters
- Max HP/Mana and regeneration are formula-driven from skills via a `stat_scaling_config.json`-style file. `[CANON]`
- **Monster template pattern:** each monster type is defined by a Base Level plus per-stat random **variance ranges**, rolled per-instance at spawn — every individual monster is a slightly different roll of its template. `[CANON]` The same weighted-roll pattern extends to visual parts (§12) and loot.

### 13.4 Items
- **Nine quality tiers**, Crude → Apex. `[CANON]`
- Equipment uses a **compact string encoding** with Primary/Secondary/Tertiary component blocks (an item is fully describable as a short structured string). `[CANON]` Preserve this pattern: it makes items cheap to store, transmit, and diff.
- **Loot tables** are weighted drop rolls defined in config. `[CANON]`
- **Item quality — three independent levers** `[CONFIRMED]`:
  1. **Material luck:** gathered materials carry their own rolled grade (gathering Precision Grade can influence it).
  2. **Execution skill:** crafting-time Precision Grade influences roll quality/affix odds.
  3. **Post-craft investment:** spend additional materials to push/reroll specific affixes on a finished item.
  Design intent: no single bad outcome is a dead end; a poor roll can be invested into, a great material survives sloppy execution partially, etc. "It may take hundreds of attempts to get a high percentile roll, then skill to hit every tick with perfect timing, then get the correct rolls on the created product. Then using more materials to change rolls or their values." Crafting materials thereby become a PoE-orb-like tradeable currency class in their own right.
  - "Some outcomes may not be worth improving depending on the material cost of changing it. I want these costs to be easy to tune." `[CONFIRMED]` — every cost curve here is config.

## 14. Death & Risk `[CONFIRMED]`

- **Severity is zone/floor-flag-dependent, not universal:** ranges from full item loss in the most dangerous areas to partial loss elsewhere. The same flag layer that governs PvP legality (§15) governs death severity — one flag system, two consumers.
- `[OPEN]` Exact "partial loss" split (equipped protected vs. inventory drops, or otherwise) — do not assume; confirm before implementing.
- **Respawn:** players can rest and/or set a respawn point. Hard requirement, verbatim: "We don't want to punish death with 2 days of real time travel." `[OPEN]` exact mechanic (one system or two; any limits on setting points near danger). Known tension to resolve deliberately when defining it: unrestricted respawn points dilute floor-height risk (§4.3); the guaranteed down-exit (§4.1) already provides a no-death de-escalation path, which softens how much the respawn system must carry.
- Item loss on death is an economy sink and must be tracked as one (§22).

## 15. PvP `[CONFIRMED direction, mechanics deferred]`

- **No separate PvP system.** PvP is the same combat resolution path as PvE with a human target — "I just viewed pvp as a two player combat instead of a player versus monster combat." Gated by a targeting/legality flag layer per floor/zone.
- Deliberately undefined until base combat is playable and feels right: "It can be bolted on and refined. I didn't want to define the mechanics until I could actually test it." Do not design PvP rules speculatively.
- Faction territory contests (§17) are expected to resolve through this same combat path.

## 16. Trade — four distinct mechanisms `[CONFIRMED, owner-specified in detail]`

Not variations of one system; they differ in fungibility handling, reach, pricing model, and tax. All tax rates are config.

### 16.1 Face-to-face — direct, both present, **free, no tax**.
### 16.2 Market
Raw materials/resources, **local** reach — "merchants gather to sell materials they have collected locally." **~1% seller tax.** Defining feature: **asynchronous** — list goods, log off, they sell while you're away.
### 16.3 Exchange
Raw materials/resources, **global** reach (ranged trading with distant players). **Algorithmic pricing via a random-walk ledger** with a real order book (buy orders / sell orders) — not player-set listing prices. **5–8% tax** as the price of reach (`[INFERRED]` seller-paid for consistency; owner has not explicitly confirmed the split for this venue). **Each material grade is its own priced instrument**: select the base item ("Iron Ore"), then grade-specific listings are visible beneath it — every grade §13.4 produces has a real trading venue. `[OPEN]` The exact random-walk algorithm (how order volume moves the ledger price).
### 16.4 Auction House
Equipment and non-fungible rolled items — things a ledger cannot price because affix rolls make each unique. Full timed auction: seller sets buy-now price and/or reserve floor plus a duration ("a full fledged auction that respects item attributes"). **5–8% seller tax PLUS a buyer-paid shipping cost scaling with distance** (in the tower model: floor-distance between seller and buyer). Consequence, intended: hauling items to a high-traffic junction floor before listing is a rational strategy (buyers there pay less shipping) — merchants who optimize logistics profit more, and **trade hubs emerge at junction floors** as a measurable pattern rather than being designated.

### 16.5 High-denomination sink items `[CONFIRMED intent]`
Deliberately scarce tradable items whose purpose is to be transaction vehicles large enough that a percentage tax on their sale drains wealth concentrated at the top of the distribution — "if 1 billion gold is 'a lot' we need items that can be worth a potential 1 billion gold so we can scrape the taxes off of it… when it is sold at auction." A flat tax on ordinary trade volume cannot reach concentrated idle wealth; only a big taxable transaction can. **Release rate of these items is a tunable config lever, adjusted against observed wealth-distribution data (§22).** Too few → whale wealth sits stagnant and undrainable; too many → none stay scarce enough to be worth a fortune. Precedent: OSRS Grand Exchange tax (~2%, destroyed) + its targeted Item Sink. Precision note: tradable *items* can serve this role; earned *status/titles* should not be tradable (purchasability destroys the signal that gives status its value) — if a status reward needs trade value, attach a tradable token, not the status itself.

## 17. Social Structures `[CONFIRMED]`

Three distinct mechanics, not three names for grouping:
- **Parties** — for shared content. Efficiency multiplier only; the game stays solo-viable (Design Identity #5).
- **Guilds** — persistent social groups/community identity.
- **Factions** — opposing-interest groups tied to contested content. Owner's concrete example: "faction A wants a mining area that faction B wants too; the winner secures the mining area for a period of time" — **time-limited control**, so contests renew rather than settle permanently. Junction floors (§4.2) are the natural contested territory. `[OPEN]` The contest-resolution mechanic (pure combat, accumulation/investment, hybrid).

## 18. Special Event Pattern: Secret-Purpose Item `[CONFIRMED — one-off/rare, not recurring]`

Release a cheap, multi-source-obtainable item with an interesting name, distinct text color, and **no stated purpose**. After a duration, announce: whoever turns in the most wins a single unique reward (winner-take-all).
- Secrecy forces *speculative* accumulation (revealed purpose = solvable arithmetic = dead tension).
- Single winner means no "safe amount" exists until reveal.
- The reveal is a clean one-shot economic sink: a previously-worthless item exits the economy in bulk, having distorted nothing beforehand.
- **Implementation is the hard part, not the design:** the turn-in count must be server-authoritative, atomic, and tamper-proof from day one. A guaranteed-unique reward is the single highest-incentive exploit/dupe/multi-account target in the entire game, and because the event is one-off, there is no patch-it-next-time. Anti-bot defenses (§21) get stress-tested here.
- `[OPEN]` Leaderboard visible during the event vs. hidden until reveal — one config line, consequential, decide deliberately.
- If tradable afterward, the reward doubles as a high-denomination sink vehicle (§16.5). The owner deeply understands discontinued-rare economics (RS party hats, WoW Spectral Tiger) — that dynamic is intended, not accidental.

## 19. Config-Driven Everything `[CONFIRMED — load-bearing for the whole collaboration]`

- **Every content definition** (monster, plant, resource node, item base, ability, talent, skeleton part-pool, vote category) is a JSON file matching a fixed schema. Engine code interprets any file of the right shape and NEVER special-cases an entity by name/ID. `if entity.id == "goblin"` anywhere in engine code is an architecture violation.
- **Every tunable number** (XP rates, tax rates, respawn times, precision windows, spawn weights/caps, travel prices, reroll cost curves, danger curve, dilation thresholds, high-denomination release rate) lives in config, loaded at worker startup. V1 bar: edit JSON, restart worker. Hot-reload is a stretch goal, not a blocker.
- **The admin's entire workflow is editing these files.** The coding agent's standing obligation: a config-only change is ALWAYS safe in isolation. If changing a number can break code, the architecture is wrong — fix the architecture.

## 20. Interaction Verb Vocabulary `[CONFIRMED]`

Abilities/talents/interactions are compositions of a **small, fixed set of primitive verbs** (e.g., `deal_damage`, `apply_status`, `consume_resource`, `modify_stat`, `spawn_entity`, `grant_precision_bonus`). New content = new *combination* of existing verbs, expressible in config. New *verbs* are rare, deliberate engine additions — resist adding one whenever a combination could serve; every verb is a permanent support burden, every combination is free. Owner's summary of the whole pattern: "making some sort of configuration on a json that lists every property of the new thing. Then something that defines what it does in certain interactions. I just want this to be easy."

**Content dependency graph (free consequence, build the query tooling):** recipes, loot tables, and actions reference content by ID, forming a queryable graph. "What does nerfing Iron Ore touch?" must be answerable as a graph query (1–2 hops out: recipes consuming it, loot tables sharing its weight pool — note lowering one weight arithmetically raises the others' relative odds — and downstream craftables). Also powers **sink-gap detection**: an item with almost no incoming recipe/sink edges is visibly incomplete content — the fix is designing it a purpose, not buffing its stats. Owner: "That can help me design purpose for underutilized items."

---

# PART VI — BUSINESS MODEL

## 21. Monetization & Anti-Bot `[CONFIRMED]`

- **~$5/month subscription lifts a messaging/chat cap. Gameplay is NEVER gated.** Free accounts fully play — explore, fight, gather, craft, trade — under a message-volume limit. "This allows people that can't afford it to play for free."
- **Two-front bot defense, owner's explicit strategy:** account-creation defenses (CAPTCHA, verification, rate limits) raise the cost of *making* bot accounts; the messaging paywall raises the cost of *operating* them profitably (spam/gold-selling business models need cheap mass messaging). Precedent: WoW Starter Edition and F2P RuneScape both restrict chat on unpaid accounts for exactly this reason.
- **Cost model sanity check (assumptions, not guarantees):** infrastructure cost tracks **peak concurrent players**, not registered accounts (offline account = a database row). Tick-based simulation + dumb client + diff-based state = a few KB Redis per live player, small periodic JSON deltas, arithmetic-only compute. At ~10–15k peak concurrent, total infra plausibly sits in the very low thousands of dollars/month or less — a rounding error against subscription revenue at any meaningful conversion. Re-estimate against real concurrency once live. The owner's target scale framing: up to ~100k registered, ~25% subscribed.

## 22. Data & Analytics `[CONFIRMED — a first-class goal, not telemetry]`

The owner explicitly wants to study the game's society and economy ("data on economy, territory, pvp, war… how societies form and transform"), use the data for evidence-based balancing, and potentially productize aggregate findings. Model: CCP's published EVE economic reports; intellectual grounding: Castronova's virtual-worlds economics (virtual economies are real economies on different infrastructure).

### 22.1 Pipeline
A separate consumer on the event bus (never in the tick path) rolls raw events into **time-bucketed aggregates**. Raw per-event logs are discarded or short-lived; aggregates are the product. **Every rollup is tagged with:** floor/tower, **config version active at the time**, and **ticks since the last relevant config change** — without the last two, no shift is attributable ("I understand balancing changes alters perception and behavior. I think that that is what will be interesting" — patches are the natural experiments; this is event-study methodology, with staggered rollout across floors/towers as a future control-group option).

### 22.2 What to track
- **Economy:** configured-vs-observed gather rates per resource/floor/skill-bracket (gaps are diagnostics: lagging = bottleneck/avoidance, exceeding = exploit); per-item faucet/sink balance (drives inflation, not price alone); price index + volume/velocity **separately per trade venue**; wealth distribution (Gini, stored as scalar only); high-denomination item release/sale tracking against the wealth tail; Community Fund tallies split intentional-vs-default (§24); sink-gap query results.
- **Combat:** time-to-kill *distributions* (not averages) per monster; death and retry-after-death rates (low completion + high retry = well-tuned-hard; low completion + low retry = bad friction); PvP kills per floor crossed with resource-contest status and death-severity flags; Precision Grade engagement rate and grade distribution (is the core timing mechanic landing?).
- **Territory:** control-change and contest frequency per floor (junction floors especially); faction win rates and control durations; climb-height/frontier progression over time; trade-hub emergence from Auction House shipping-distance data.
- **Participation:** observed vs. reward-predicted participation per activity (the gap above the reward-justified baseline is the honest "people enjoy this" signal — raw participation confounds enjoyment with reward level); skill-bracket mix per activity. The owner is an experienced judge of player motivation — the data informs, it does not replace, that judgment. `[CONFIRMED]` ("I have the experience to determine motivations behind these techniques.")
- **Social:** party formation/size and correlation with difficult content (checks "efficiency multiplier, not gate"); guild size/churn; trade-network topology.
- **Accessibility:** detection behavior around color-differentiated rares with hue as the *only* variable (identical shape/brightness/sound), against attentive-player baselines; a population gap near known CVD rates (~8% of men) flags a color-only cue to fix with a redundant cue.

### 22.3 Honest scope
What transfers from this data: methodology (event studies, staggered rollouts) and mechanism discovery. What does not: specific rates (perfect enforcement, no black market, no survival stakes, no credit/banking, self-selected population). Defensible framing if productized: a testbed for human decision-making under constraint, incentive, and incomplete information — "human + economy, human + vote, human + conflict" — not a policy oracle. The owner endorses this framing explicitly.

## 23. The Identity Boundary `[CONFIRMED — architectural, not policy]`

- Player IDs/names are used freely in **operational** systems: sessions, moderation, ownership, in-game tributes/credit. "Player id's and names obviously need to be stored. Just not packaged into a sellable or usable product (outside of things like in-game tributes etc)."
- **The export/aggregation layer is the one seam no player identifier ever crosses.** The aggregator may transiently READ per-account values (a Gini needs them) but is structurally forbidden from WRITING any identifier downstream. Only scalars/aggregates persist on the product side.
- **Minimum-group-size suppression:** any bucket from fewer than N distinct players is suppressed or merged before publication — aggregation of one is not anonymity.
- Behaviorally-inferred physiological traits (the CVD estimate) get the stricter treatment: population-level output only, never per-player inference, ever.
- Public claim this architecture must keep literally true: "any data we collect is in relation to the game society and happenings as a whole. We don't collect data that stores, specifies or indicates anyone's identity."
- Commercial use of even de-identified data is a ToS/privacy-policy disclosure matter (GDPR/CCPA) — lawyer's pass before productizing; not a build blocker.

## 24. Community Development Fund `[CONFIRMED]`

Players direct **visual/cosmetic** development priorities with their taxes — explicitly never gameplay/mechanics/balance direction, which stays solely the admin's. "I don't want to give them too much power in the direction of the game, but they should have a lot of power regarding how it looks."
- **One unified coffer** — all venue taxes pool together; no per-venue split. The currency is **genuinely destroyed** (it's a real sink first); "spending" is a reporting layer: the admin logs accrual per category and periodically ships a themed visual update with a public "expense report."
- **Standing preference:** each player selects a category; every tax they pay while it's active logs to it. No separate voting action.
- **Admin-chosen default category** `[CONFIRMED]`: players who never choose log to the admin's default. Purpose is *measurement*, not bias — it separates **intentional** votes from **passive** volume. Track every category's total as two figures (intentional vs. default-driven), never blended.
- Influence is proportional to tax paid (heavy traders steer more) — inherent to the design as specified; owner is aware.
- Tonal intent, keep it: this is meant to be a little funny — public expense reports, players good-naturedly griping about "not getting as much for their tax money as they used to."
- `[OPEN]` allocation logic per period (top category wins vs. proportional), cadence, starter category list (categories are config, per §19).

---

# PART VII — BUILD PLAN

## 25. First Build Target `[CONFIRMED scope philosophy: strong base first, feel the loop, then layer]`

The owner's stated bar: "I want a traversable hex world. I want combat. I want resources. I want basic versions of the skills outlined that can be refined. I want it to run on a server from the start so I can just scale the actions."

**Build exactly this first:**
- One tower, a handful of floors, one GEP worker process. Floor generation deterministic from day one (shared-PRNG requirement, §11.1, is NOT deferrable — retrofitting determinism is brutal).
- 1 Hz tick loop with the action-queue/`Target_Tick` pattern. Tick dilation can be stubbed (broadcast a fixed 1.0 duration) but the tick-duration field exists in the protocol from day one.
- Hex-tile movement within floors; up/down exits between floors.
- Combat vs. 1–2 monster templates implementing the full 6-step flow (§13.2) with all constants in config.
- 1–2 gatherable resources; XP into at least one combat and one non-combat skill per §13.1.
- ALL entities from JSON config — zero hardcoded per-entity logic even at this scale. This discipline is cheapest on day one and ruinous to retrofit.
- Dumb web client: renders state, sends intent, no prediction; simplest acceptable rendering (CSS/Canvas2D); a single default character look.
- One simple death rule (single severity, fixed respawn) as placeholder for §14.
- Server-authoritative from the first commit — even solo on localhost, the client fakes nothing.

**Explicitly deferred (layer on after the base loop is fun):** Precision Grade bonuses; the three-lever item quality system; multi-worker scaling and tick dilation activation; the four-venue trade system; parts-based visuals and character customization; rare-cap tuning; parties/guilds/factions; monetization; the Community Fund; special events; the analytics pipeline; travel pricing; floor dormancy/catch-up (trivial with few floors, but design the state-diff format for it now).

## 26. Standing Priorities When Anything Is Ambiguous

1. Server authority is never compromised for convenience.
2. Config-editability is never compromised — if a change requires code, ask whether it should.
3. One-directional data flow (§10) is never violated.
4. Determinism of generation is never approximated.
5. When a mechanic could be a special case or a general rule, it's a general rule.
6. When something is `[OPEN]`, ask the owner — do not fill gaps with plausible guesses. The owner is precise about intent and responsive to direct questions; assumption is the failure mode that killed the previous attempts at this project.
