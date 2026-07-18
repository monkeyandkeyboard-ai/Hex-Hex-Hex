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
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.floor_manager import FloorManager
from gep.stats import compute_max_hp, compute_max_mana
from gep.systems import combat_system, floor_exits, gathering, movement
from gep.systems import inventory_system
from gep.tick import TickEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
HOST = "0.0.0.0"
PORT = 8765
TICK_HZ = 1.0


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


def build_floor_state(floor_number: int, cfg: ConfigStore, on_change_floor) -> tuple[FloorState, TickEngine]:
    layout = generate_floor(
        tower_id="tower-a",
        floor_number=floor_number,
        global_seed=cfg.world["global_seed"],
        ruleset=cfg.floor_ruleset,
        archetypes=cfg.floor_archetypes,
        biomes=cfg.biomes,
    )
    floor = FloorState.from_layout(layout)

    for i, spawn in enumerate(layout.monster_spawns):
        template_id = spawn["template_id"]
        template = cfg.monsters[template_id]
        monster_id = f"{template_id}_{floor_number}_{i}"
        monster = roll_monster(monster_id, template, cfg.stat_scaling)
        monster.floor_number = floor_number
        monster.tile = spawn["tile"]
        floor.monsters[monster_id] = monster

    engine = TickEngine(tick_duration=1.0 / TICK_HZ)

    movement.register(engine, floor)
    gathering.register(engine, floor, cfg.resources, cfg.xp_table)
    combat_system.register(
        engine, floor,
        weapons=cfg.weapons,
        monsters_cfg=cfg.monsters,
        combat_constants=cfg.combat_constants,
        xp_rates=cfg.xp_rates,
        xp_table=cfg.xp_table,
        stat_scaling=cfg.stat_scaling,
    )
    floor_exits.register(engine, floor, on_change_floor)
    inventory_system.register(engine, floor, cfg.weapons)

    return floor, engine


def floor_snapshot(floor: FloorState, tick: int, tick_duration: float, xp_table: dict, biomes: dict) -> dict:
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
        "regions": {f"{q},{r}": bid for (q, r), bid in layout.regions.items()},
        "roads": [list(t) for t in layout.roads],
        "biomes": {bid: {"color": b["color"], "display_name": b["display_name"]} for bid, b in biomes.items()},
        "resource_nodes": {f"{q},{r}": rid for (q, r), rid in floor.resource_nodes.items()},
        "monsters": {
            mid: {
                "id": mid,
                "template_id": m.template_id,
                "tile": list(m.tile),
                "hp": m.hp,
                "max_hp": m.max_hp,
                "alive": m.alive,
            }
            for mid, m in floor.monsters.items()
        },
        "players": {
            pid: {
                "id": pid,
                "name": p.name,
                "tile": list(p.tile),
                "hp": p.hp,
                "max_hp": p.max_hp,
                "skills": _skills_payload(p, xp_table),
                "inventory": p.inventory_snapshot(),
                "equipment": p.equipment.to_dict(),
            }
            for pid, p in floor.players.items()
        },
    }


async def run_server():
    cfg = ConfigStore(CONFIG_DIR)

    # Floors are built on demand and cached. Each floor owns its own tick
    # engine (its own action queue), so a respawn scheduled on floor 3 never
    # touches floor 1. Every instantiated floor is stepped each tick.
    def _build(floor_number, on_change_floor):
        log.info("Built floor %d", floor_number)
        return build_floor_state(floor_number, cfg, on_change_floor)

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

        ss = cfg.stat_scaling
        saved = db.load_player(player_id)
        max_hp = compute_max_hp(1, ss)
        max_mana = compute_max_mana(1, ss)

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

        player = Player(
            id=player_id,
            name=username,
            tower_id="tower-a",
            floor_number=1,
            tile=(0, 0),
            hp=max_hp,
            max_hp=max_hp,
            mana=max_mana,
            max_mana=max_mana,
            weapon_id=equipment.main_hand or "fists",
            skills=skills,
            equipment=equipment,
            inventory=inventory,
        )

        floor = manager.add_player(player, 1)
        engine = floors[1][1]
        connections[player_id] = ws
        log.info("Player %s (%s) connected", username, player_id[:8])

        try:
            await ws.send(json.dumps(floor_snapshot(floor, engine.tick, engine.tick_duration, cfg.xp_table, cfg.biomes)))

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

    async def tick_loop():
        import time
        tick_interval = 1.0 / TICK_HZ
        next_tick = time.monotonic() + tick_interval

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
                            await ws.send(json.dumps(
                                floor_snapshot(fs, fe.tick, fe.tick_duration, cfg.xp_table, cfg.biomes)
                            ))
                        except websockets.exceptions.ConnectionClosed:
                            pass
                pending_snapshots.clear()

            # Broadcast each floor's tick result to the players standing on it.
            for fl_num, (floor_state, floor_engine) in list(floors.items()):
                recipients = [pid for pid, ws in connections.items() if player_floor.get(pid) == fl_num]
                if not recipients:
                    continue
                result = results[fl_num]
                for pid in recipients:
                    p = floor_state.players.get(pid)
                    if p:
                        result.events.append({
                            "type": "player_update",
                            "player_id": pid,
                            "hp": p.hp,
                            "max_hp": p.max_hp,
                            "skills": _skills_payload(p, cfg.xp_table),
                            "inventory": p.inventory_snapshot(),
                            "equipment": p.equipment.to_dict(),
                        })
                broadcast = json.dumps({
                    "tick": result.tick,
                    "tick_duration": result.tick_duration,
                    "events": result.events,
                })
                await asyncio.gather(
                    *(connections[pid].send(broadcast) for pid in recipients),
                    return_exceptions=True,
                )

    log.info("GEP server starting on ws://%s:%d", HOST, PORT)
    async with websockets.serve(handle_client, HOST, PORT):
        await tick_loop()


def main():
    from serve_client import start_in_thread, HTTP_PORT
    start_in_thread()
    log.info("Client served at http://0.0.0.0:%d", HTTP_PORT)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
