"""GEP server entrypoint. Starts a 1 Hz tick loop and a WebSocket listener.

Run with:  python -m gep.server  (from server/)

Auth flow:
  1. Client connects with ?token=<session_token> in the WS URL
  2. Server validates token -- if valid, sends auth_ok immediately
  3. If no/invalid token, sends auth_required; client sends login/register
  4. On success, server sends auth_ok + floor_snapshot
  5. Session token stored client-side in localStorage for auto-resume

Wire shape (compendium §11):
  client → server:  {"intent_type": "...", ...}  (post-auth)
  server → client:  {"tick": N, "tick_duration": 1.0, "events": [...]}
"""
import asyncio
import json
import logging
import pathlib
from urllib.parse import parse_qs, urlparse

import websockets

from gep import db
from gep.config_loader import ConfigStore
from gep.entities import Equipment, Player, Skills, roll_monster
from gep.items import is_instance
from gep.floorgen import generate_floor, pack_biome_map, pack_unit_field
from gep.floor_state import FloorState
from gep.floor_manager import FloorManager
from gep.spawner import spawn_floor
from gep.stats import compute_max_hp, compute_max_mana
from gep.systems import combat_system, floor_exits, gathering, movement
from gep.systems import inventory_system, monster_ai, regeneration, respawn
from gep.tick import TickEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
HOST = "0.0.0.0"
PORT = 8765
TICK_HZ = 1.0


def build_player(player_id: str, username: str, saved: dict | None, cfg) -> Player:
    """Construct the live player for a session from their saved record.

    Vitality is restored, not reset: a character who logged out injured logs
    back in injured. Max HP/mana are derived from the *loaded* skill levels,
    so constitution actually raises the ceiling -- previously they were
    computed from a hardcoded level 1 before the save was read, which meant
    levelling constitution never raised max HP at all.
    """
    ss = cfg.stat_scaling

    skills = Skills()
    skills.non_combat = {s: 1 for s in cfg.skills["non_combat_skills"]}
    skills.non_combat_xp = {s: 0.0 for s in cfg.skills["non_combat_skills"]}

    equipment = Equipment()
    inventory: dict[int, dict | None] = {}

    if saved:
        skills.combat = {**skills.combat, **saved["combat_levels"]}
        skills.combat_xp = {**skills.combat_xp, **saved["combat_xp"]}
        skills.non_combat = {**skills.non_combat, **saved["non_combat_levels"]}
        skills.non_combat_xp = {**skills.non_combat_xp, **saved["non_combat_xp"]}
        equipment = Equipment.from_dict(saved["equipment"])
        inventory = {int(k): v for k, v in saved["inventory"].items()}

    max_hp = compute_max_hp(skills.combat.get("constitution", 1), ss)
    max_mana = compute_max_mana(skills.combat.get("mana_attunement", 1), ss)

    hp = _restore_vitality(saved.get("hp") if saved else None, max_hp, player_id, "hp")
    mana = _restore_vitality(saved.get("mana") if saved else None, max_mana, player_id, "mana")

    return Player(
        id=player_id,
        name=username,
        tower_id="tower-a",
        floor_number=1,
        tile=(0, 0),
        hp=hp,
        max_hp=max_hp,
        mana=mana,
        max_mana=max_mana,
        # An empty main_hand is a state, not an absence: it resolves to the
        # configured default equipment id and goes down the same registry
        # lookup as a sword would.
        weapon_id=equipment.main_hand or cfg.default_equipment_state,
        skills=skills,
        equipment=equipment,
        inventory=inventory,
    )


def _restore_vitality(saved_value, maximum: float, player_id: str, what: str) -> float:
    """Saved current-value -> the value to start the session at.

    None means no saved value (new character, or one predating vitality
    persistence): start full. Otherwise clamp into range, because the ceiling
    can move between sessions -- a constitution level-up raises max HP, and a
    balance change to stat_scaling.json can lower it.

    A non-positive saved value would strand the character dead on arrival:
    nothing damages players yet and there is no respawn flow (compendium §14
    leaves it [OPEN]), so they would be unrecoverable. Restore to full and say
    so, rather than inventing death-on-login semantics here.
    """
    if saved_value is None:
        return maximum
    if saved_value <= 0:
        log.warning("player %s loaded with %s=%s; restoring to full pending "
                    "death/respawn rules (compendium 14)", player_id, what, saved_value)
        return maximum
    return min(float(saved_value), maximum)


def save_players(players) -> int:
    """Persist each player, returning how many writes succeeded.

    Saving only in the disconnect handler means a crash, a kill, or a pulled
    plug loses everything since login, because the `finally` never runs. The
    autosave interval bounds that loss; this is the part that does the work.

    One player failing must not abandon the rest, and must never take the
    tick loop down -- the next interval will try again.
    """
    saved = 0
    for player in players:
        try:
            db.save_player(player)
            saved += 1
        except Exception:
            log.exception("failed to save player %s", getattr(player, "id", "?"))
    return saved


def _xp_next_level(current_xp: float, xp_table: dict) -> float:
    """Cumulative XP required to reach the next level above current_xp."""
    sorted_levels = sorted(int(k) for k in xp_table)
    for lvl in sorted_levels:
        threshold = xp_table[str(lvl)]
        if threshold > current_xp:
            return threshold
    return xp_table[str(sorted_levels[-1])]


def _skills_payload(player: Player, xp_table: dict) -> dict:
    """Rich skills dict sent to the client: level, current XP, XP for next level."""
    out = {}
    for skill, level in {**player.skills.combat, **player.skills.non_combat}.items():
        xp = player.skills.combat_xp.get(skill) or player.skills.non_combat_xp.get(skill, 0.0)
        out[skill] = {
            "level": level,
            "xp": round(xp, 1),
            "xp_next": round(_xp_next_level(xp, xp_table), 1),
        }
    return out


def build_floor_state(floor_number: int, cfg: ConfigStore, on_change_floor,
                      on_relocate=None) -> tuple[FloorState, TickEngine]:
    layout = generate_floor(
        tower_id="tower-a",
        floor_number=floor_number,
        global_seed=cfg.world["global_seed"],
        ruleset=cfg.floor_ruleset,
        archetypes=cfg.floor_archetypes,
        biomes=cfg.biomes,
        prefabs=cfg.prefabs,
    )
    plan = spawn_floor(
        layout,
        biomes=cfg.biomes,
        spawn_ruleset=cfg.spawn_ruleset,
        spawn_seed=cfg.world["spawn_seed"],
        prefabs=cfg.prefabs,
    )
    floor = FloorState.from_layout(layout, resource_nodes=plan.resource_nodes)

    for i, spawn in enumerate(plan.monster_spawns):
        template_id = spawn["template_id"]
        template = cfg.monsters[template_id]
        monster_id = f"{template_id}_{floor_number}_{i}"
        monster = roll_monster(
            monster_id, template, cfg.stat_scaling,
            reward_table_override=spawn.get("reward_table_override"),
        )
        monster.floor_number = floor_number
        monster.tile = spawn["tile"]
        floor.monsters[monster_id] = monster

    engine = TickEngine(tick_duration=1.0 / TICK_HZ)

    gathering.register(engine, floor, cfg.resources, cfg.xp_table)

    # Registration order follows the callback seams, not preference:
    #   monster_ai -> combat  (combat reports damage, behaviour reacts)
    #   combat -> movement    (movement reports a move, combat disengages)
    # Each system is handed a function and knows nothing else about the other.
    regeneration.register(engine, floor, cfg.stat_scaling)
    respawn.register(engine, floor, save_player=db.save_player,
                     on_relocate=on_relocate)
    notify_threat = monster_ai.register(engine, floor, monsters_cfg=cfg.monsters)
    break_engagement = combat_system.register(
        engine, floor,
        weapons=cfg.weapons,
        monsters_cfg=cfg.monsters,
        combat_constants=cfg.combat_constants,
        xp_rates=cfg.xp_rates,
        xp_table=cfg.xp_table,
        rewards=cfg.rewards,
        stat_scaling=cfg.stat_scaling,
        items=cfg.items,
        weapon_classes=cfg.weapon_classes,
        power_scaling=cfg.power_scaling,
        on_threat=notify_threat,
    )
    movement.register(engine, floor, on_move=break_engagement)
    floor_exits.register(engine, floor, on_change_floor)
    inventory_system.register(engine, floor, cfg.weapons,
                              cfg.default_equipment_state, cfg.items)

    return floor, engine


def resolve_item(item_id: str, items, resources: dict) -> dict:
    """Client-facing description of an item -- rolled name, type, tier and
    stats for equipment; just a display name for a stackable material.
    Resolved once at the server boundary so the client never has to parse an
    instance string itself (gep/items.py stays the only thing that does).
    """
    if is_instance(item_id):
        stats = items.runtime_stats(item_id)
        return {
            "item_id": item_id,
            "display_name": stats["display_name"],
            "type": stats["type"],
            "tier": stats["tier"],
            "equipment_slot": stats["equipment_slot"],
            "damage_min": stats["damage_min"],
            "damage_max": stats["damage_max"],
            "speed_ticks": stats["speed_ticks"],
            "armor": stats["armor"],
            "stats": stats["stats"],
            "mods": stats["mods"],
        }
    resource = resources.get(item_id)
    display_name = resource["display_name"] if resource else item_id.replace("_", " ")
    return {"item_id": item_id, "display_name": display_name}


def resolve_inventory(snapshot: list, items, resources: dict) -> list:
    return [
        None if slot is None else
        {"item": resolve_item(slot["item_id"], items, resources), "quantity": slot["quantity"]}
        for slot in snapshot
    ]


def resolve_equipment(equipment: dict, items, resources: dict) -> dict:
    return {
        slot: (resolve_item(item_id, items, resources) if item_id else None)
        for slot, item_id in equipment.items()
    }


def resolve_event_items(event: dict, items, resources: dict) -> dict:
    """Rewrite an event's raw `inventory`/`equipment` payloads (built by
    systems that only know item ids) into client-facing resolved form. One
    boundary transform rather than every system re-deriving it."""
    if isinstance(event.get("inventory"), list):
        event["inventory"] = resolve_inventory(event["inventory"], items, resources)
    if isinstance(event.get("equipment"), dict):
        event["equipment"] = resolve_equipment(event["equipment"], items, resources)
    return event


def floor_snapshot(
    floor: FloorState, tick: int, tick_duration: float, xp_table: dict, biomes: dict,
    items, resources: dict,
) -> dict:
    layout = floor.layout
    return {
        "type": "floor_snapshot",
        "tick": tick,
        "tick_duration": tick_duration,
        "floor_number": layout.floor_number,
        "radius": layout.radius,
        "archetype": layout.archetype,
        "safe": layout.safe,
        "up_exit": list(layout.up_exit),
        "down_exit": list(layout.down_exit) if layout.down_exit else None,
        # Structural payload, packed one byte per tile in canonical tile order
        # (tiles_in_radius): biome index + quantised elevation/roughness.
        "biome_legend": list(biomes.keys()),
        "biome_map": pack_biome_map(layout.regions, layout.tiles, list(biomes.keys())),
        "elevation": pack_unit_field(layout.elevation, layout.tiles),
        "roughness": pack_unit_field(layout.roughness, layout.tiles),
        "roads": [list(t) for t in layout.roads],
        # Crossings only (gep/passability.py). The blocked set is deliberately
        # NOT shipped: the client already has biome_map and the biome table, and
        # `passable` below makes every blocked tile derivable from those. Sending
        # it as coordinates cost 100KB on a chamber floor, where 84% of tiles are
        # barrier -- "sparse" stopped being true the moment barriers got big.
        # Crossings stay because a carved ford is no longer water, so nothing in
        # the biome data says where one is.
        "crossings": [list(t) for t in layout.crossings],
        # Reserved tile types (gep/tiles.py): sparse tile -> id. up_exit and
        # down_exit above are the same two tiles, kept because the exit intent
        # and pathing address them by coordinate; this is how they render.
        "tile_types": {f"{q},{r}": t for (q, r), t in layout.tile_types.items()},
        "prefabs": [
            {
                "prefab_id": p.prefab_id,
                "tile_sprites": {f"{q},{r}": s for (q, r), s in p.tile_sprites.items()},
            }
            for p in layout.prefabs
        ],
        "biomes": {
            bid: {
                "display_name": b["display_name"],
                "hsl": b.get("hsl", {"h": 0, "s": 0, "l": 20}),
                # One bool per biome replaces a per-tile blocked array: the
                # client crosses this with biome_map to know which tiles it
                # must not path into. Movement remains server-authoritative --
                # this is so the client can decline to draw a move preview
                # into a cliff, not so it can decide the move itself.
                "passable": b["passable"],
                # Optional: how the client textures this biome's tiles. Purely
                # presentational, and optional on purpose -- a biome without
                # it still renders, just flat. Shipped rather than hardcoded
                # client-side so terrain look stays a config decision.
                "texture": b.get("texture"),
            }
            for bid, b in biomes.items()
        },
        "resource_nodes": {f"{q},{r}": rid for (q, r), rid in floor.resource_nodes.items()},
        "monsters": {
            mid: {
                "id": mid,
                "template_id": m.template_id,
                "tile": list(m.tile),
                "hp": m.hp,
                "max_hp": m.max_hp,
                "alive": m.alive,
                "visual": m.visual,
                "facing": m.facing,
            }
            for mid, m in floor.monsters.items()
        },
        "players": {
            pid: {
                "id": pid,
                "name": p.name,
                "tile": list(p.tile),
                "facing": p.facing,
                "hp": p.hp,
                "max_hp": p.max_hp,
                "skills": _skills_payload(p, xp_table),
                "inventory": resolve_inventory(p.inventory_snapshot(), items, resources),
                "equipment": resolve_equipment(p.equipment.to_dict(), items, resources),
            }
            for pid, p in floor.players.items()
        },
    }


def build_broadcasts(results, floors, player_floor, connected, cfg) -> list[tuple[list[str], dict]]:
    """Turn one tick's per-floor results into (recipients, payload) pairs.

    Pulled out of run_server's loop and kept free of sockets and awaits so the
    routing can be tested without a live server -- the bug this shape prevents
    was unreachable from any test while it lived inline.

    `results` is keyed by the floors that were actually stepped, and this
    iterates *that*, not `floors`. Taking the stairs builds the destination
    floor during the step, so by broadcast time `floors` can hold a floor with
    no tick result; indexing `results` by it raised KeyError and killed the
    server loop. The arriving player loses nothing -- change_floor put them in
    pending_snapshots and they get a full snapshot, which says strictly more
    than a tick result. Their new floor broadcasts normally from the next tick.
    """
    out: list[tuple[list[str], dict]] = []
    for fl_num, result in results.items():
        floor_state, _ = floors[fl_num]
        recipients = [pid for pid in connected if player_floor.get(pid) == fl_num]
        if not recipients:
            continue
        # Systems that emit inventory/equipment only know item ids; resolve
        # them into client-facing form once here rather than teaching every
        # system about the item registry and resources.
        result.events = [resolve_event_items(e, cfg.items, cfg.resources) for e in result.events]
        for pid in recipients:
            p = floor_state.players.get(pid)
            if p:
                result.events.append({
                    "type": "player_update",
                    "player_id": pid,
                    "hp": p.hp,
                    "max_hp": p.max_hp,
                    "skills": _skills_payload(p, cfg.xp_table),
                    "inventory": resolve_inventory(p.inventory_snapshot(), cfg.items, cfg.resources),
                    "equipment": resolve_equipment(p.equipment.to_dict(), cfg.items, cfg.resources),
                })
        out.append((recipients, {
            "tick": result.tick,
            "tick_duration": result.tick_duration,
            "events": result.events,
        }))
    return out


async def run_server():
    cfg = ConfigStore(CONFIG_DIR)

    # Floors are built on demand and cached. Each floor owns its own tick
    # engine (its own action queue), so a respawn scheduled on floor 3 never
    # touches floor 1. Every instantiated floor is stepped each tick.
    def _build(floor_number, on_change_floor, on_relocate):
        log.info("Built floor %d", floor_number)
        return build_floor_state(floor_number, cfg, on_change_floor, on_relocate)

    manager = FloorManager(_build)
    floors = manager.floors
    player_floor = manager.player_floor
    pending_snapshots = manager.pending_snapshots

    intent_queue: list[dict] = []
    connections: dict[str, websockets.ServerConnection] = {}

    manager.get_or_build(1)  # floor 1 always exists

    async def authenticate(ws) -> tuple[str, str] | None:
        """Returns (player_id, username) or None if auth fails/disconnects."""
        # Try token in query string first (auto-resume)
        qs = parse_qs(urlparse(ws.request.path).query)
        token = qs.get("token", [None])[0]
        result = db.validate_session(token) if token else None
        if result:
            player_id, username = result
            await ws.send(json.dumps({
                "type": "auth_ok",
                "player_id": player_id,
                "your_name": username,
                "session_token": token,
            }))
            return result

        # No valid token -- require credentials
        await ws.send(json.dumps({"type": "auth_required"}))

        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "auth_fail", "reason": "invalid JSON"}))
                continue

            msg_type = msg.get("type")
            username = msg.get("username", "").strip()
            password = msg.get("password", "")

            if msg_type == "register":
                player_id, err = db.register(username, password)
                if err:
                    await ws.send(json.dumps({"type": "auth_fail", "reason": err}))
                    continue
                new_token = db.create_session(player_id, username)
                await ws.send(json.dumps({
                    "type": "auth_ok",
                    "player_id": player_id,
                    "your_name": username,
                    "session_token": new_token,
                }))
                return player_id, username

            elif msg_type == "login":
                player_id, err = db.login(username, password)
                if err:
                    await ws.send(json.dumps({"type": "auth_fail", "reason": err}))
                    continue
                new_token = db.create_session(player_id, username)
                await ws.send(json.dumps({
                    "type": "auth_ok",
                    "player_id": player_id,
                    "your_name": username,
                    "session_token": new_token,
                }))
                return player_id, username

            else:
                await ws.send(json.dumps({"type": "auth_fail", "reason": "expected login or register"}))

        return None  # disconnected during auth

    async def handle_client(ws):
        auth = await authenticate(ws)
        if auth is None:
            return
        player_id, username = auth

        saved = db.load_player(player_id)
        player = build_player(player_id, username, saved, cfg)

        floor = manager.add_player(player, 1)
        engine = floors[1][1]
        connections[player_id] = ws
        log.info("Player %s (%s) connected", username, player_id[:8])

        try:
            await ws.send(json.dumps(floor_snapshot(
                floor, engine.tick, engine.tick_duration, cfg.xp_table, cfg.biomes,
                cfg.items, cfg.resources,
            )))

            async for raw in ws:
                try:
                    intent = json.loads(raw)
                    intent["player_id"] = player_id
                    intent_queue.append(intent)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({"type": "error", "reason": "invalid JSON"}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            db.save_player(player)
            manager.remove_player(player_id)
            connections.pop(player_id, None)
            log.info("Player %s saved and disconnected", username)

    def save_connected_players() -> int:
        """Flush every connected player to the database."""
        players = []
        for pid in list(connections):
            fl = player_floor.get(pid)
            if fl is None or fl not in floors:
                continue
            player = floors[fl][0].players.get(pid)
            if player is not None:
                players.append(player)
        return save_players(players)

    async def tick_loop():
        import time
        tick_interval = 1.0 / TICK_HZ
        next_tick = time.monotonic() + tick_interval

        autosave_every = int(cfg.world.get("autosave_interval_ticks", 30))
        ticks_since_autosave = 0

        while True:
            now = time.monotonic()
            sleep_for = next_tick - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            next_tick += tick_interval

            # Route each queued intent to the engine of the floor its player
            # is currently on.
            drained, intent_queue[:] = list(intent_queue), []
            per_floor: dict[int, list[dict]] = {}
            for intent in drained:
                fl = player_floor.get(intent.get("player_id"))
                if fl is not None:
                    per_floor.setdefault(fl, []).append(intent)

            # Step every instantiated floor (a use-exit handler may move a
            # player and populate pending_snapshots during its step).
            results: dict[int, object] = {}
            for fl_num, (floor_state, floor_engine) in list(floors.items()):
                results[fl_num] = floor_engine.step(per_floor.get(fl_num, []))

            if autosave_every > 0:
                ticks_since_autosave += 1
                if ticks_since_autosave >= autosave_every:
                    ticks_since_autosave = 0
                    count = save_connected_players()
                    if count:
                        log.debug("autosaved %d player(s)", count)

            if not connections:
                continue

            # Deliver a fresh snapshot to anyone who just changed floor.
            if pending_snapshots:
                for pid in list(pending_snapshots):
                    ws = connections.get(pid)
                    fl = player_floor.get(pid)
                    if ws and fl in floors:
                        fs, fe = floors[fl]
                        try:
                            await ws.send(json.dumps(floor_snapshot(
                                fs, fe.tick, fe.tick_duration, cfg.xp_table, cfg.biomes,
                                cfg.items, cfg.resources,
                            )))
                        except websockets.exceptions.ConnectionClosed:
                            pass
                pending_snapshots.clear()

            # Broadcast each floor's tick result to the players standing on it.
            # Routing is build_broadcasts' job; this only sends.
            for recipients, payload in build_broadcasts(
                results, floors, player_floor, connections.keys(), cfg
            ):
                broadcast = json.dumps(payload)
                await asyncio.gather(
                    *(connections[pid].send(broadcast) for pid in recipients),
                    return_exceptions=True,
                )

    log.info("GEP server starting on ws://%s:%d", HOST, PORT)
    try:
        async with websockets.serve(handle_client, HOST, PORT):
            await tick_loop()
    finally:
        # Ctrl+C and other orderly shutdowns: flush immediately rather than
        # discarding up to an interval's worth of progress. A hard kill still
        # falls back to the autosave interval, which is the point of having
        # one -- this just makes the common case lossless.
        count = save_connected_players()
        if count:
            log.info("saved %d player(s) on shutdown", count)


def main():
    from serve_client import start_in_thread, HTTP_PORT
    start_in_thread()
    log.info("Client served at http://0.0.0.0:%d", HTTP_PORT)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
