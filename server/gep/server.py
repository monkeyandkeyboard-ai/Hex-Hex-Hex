"""GEP server entrypoint. Starts a 1 Hz tick loop and a WebSocket listener.

Run with:  python -m gep.server  (from server/)

Wire shape (compendium §11):
  client → server:  {"intent_type": "...", ...}
  server → client:  {"tick": N, "tick_duration": 1.0, "events": [...]}

On connect: client receives full floor state so it can render immediately.
All game logic runs on the tick; WebSocket messages arriving between ticks
are queued and drained at the next tick boundary.
"""
import asyncio
import json
import logging
import pathlib
import uuid
from urllib.parse import parse_qs, urlparse

import websockets

from gep import db
from gep.config_loader import ConfigStore
from gep.entities import Player, Skills, roll_monster
from gep.floorgen import generate_floor
from gep.floor_state import FloorState
from gep.stats import compute_max_hp, compute_max_mana
from gep.systems import combat_system, floor_exits, gathering, movement
from gep.tick import TickEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CONFIG_DIR = pathlib.Path(__file__).resolve().parents[1] / "config"
HOST = "0.0.0.0"
PORT = 8765
TICK_HZ = 1.0


def build_floor_state(floor_number: int, cfg: ConfigStore) -> tuple[FloorState, TickEngine]:
    layout = generate_floor(
        tower_id="tower-a",
        floor_number=floor_number,
        global_seed=cfg.world["global_seed"],
        ruleset=cfg.floor_ruleset,
    )
    floor = FloorState.from_layout(layout)

    # Spawn initial monsters from floor layout
    for i, spawn in enumerate(layout.monster_spawns):
        template_id = spawn["template_id"]
        template = cfg.monsters[template_id]
        monster_id = f"{template_id}_{floor_number}_{i}"
        monster = roll_monster(monster_id, template, cfg.stat_scaling)
        monster.floor_number = floor_number
        monster.tile = spawn["tile"]
        floor.monsters[monster_id] = monster

    engine = TickEngine(tick_duration=1.0 / TICK_HZ)

    def on_change_floor(player_id, direction):
        pass  # V1 stub: single floor only; multi-floor routing deferred

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

    return floor, engine


def floor_snapshot(floor: FloorState, tick: int, tick_duration: float) -> dict:
    """Full state snapshot sent to clients on connect."""
    layout = floor.layout
    return {
        "type": "floor_snapshot",
        "tick": tick,
        "tick_duration": tick_duration,
        "floor_number": layout.floor_number,
        "radius": layout.radius,
        "up_exit": list(layout.up_exit),
        "down_exit": list(layout.down_exit) if layout.down_exit else None,
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
                "skills": {**p.skills.combat, **p.skills.non_combat},
            }
            for pid, p in floor.players.items()
        },
    }


async def run_server():
    cfg = ConfigStore(CONFIG_DIR)
    floor, engine = build_floor_state(floor_number=1, cfg=cfg)

    # Inbound intent queue — drained each tick
    intent_queue: list[dict] = []
    # websocket connections — player_id → ws
    connections: dict[str, websockets.ServerConnection] = {}

    async def handle_client(ws):
        # Persistent identity: client sends ?token=<uuid> in the WS URL.
        # The same token always resolves to the same saved player.
        qs = parse_qs(urlparse(ws.request.path).query)
        token = qs.get("token", [None])[0]
        player_id = token[:36] if token else str(uuid.uuid4())

        ss = cfg.stat_scaling
        saved = db.load_player(player_id)
        max_hp = compute_max_hp(1, ss)
        max_mana = compute_max_mana(1, ss)

        skills = Skills()
        skills.non_combat = {s: 1 for s in cfg.skills["non_combat_skills"]}
        skills.non_combat_xp = {s: 0.0 for s in cfg.skills["non_combat_skills"]}

        if saved:
            skills.combat = {**skills.combat, **saved["combat_levels"]}
            skills.combat_xp = {**skills.combat_xp, **saved["combat_xp"]}
            skills.non_combat = {**skills.non_combat, **saved["non_combat_levels"]}
            skills.non_combat_xp = {**skills.non_combat_xp, **saved["non_combat_xp"]}
            name = saved["name"]
        else:
            name = f"Player-{player_id[:8]}"

        player = Player(
            id=player_id,
            name=name,
            tower_id="tower-a",
            floor_number=1,
            tile=(0, 0),
            hp=max_hp,
            max_hp=max_hp,
            mana=max_mana,
            max_mana=max_mana,
            weapon_id="fists",
            skills=skills,
        )

        floor.players[player_id] = player
        connections[player_id] = ws
        log.info("Player %s connected (saved=%s)", player_id[:8], saved is not None)

        try:
            await ws.send(json.dumps({
                "type": "welcome",
                "player_id": player_id,
                "your_name": player.name,
            }))
            await ws.send(json.dumps(floor_snapshot(floor, engine.tick, engine.tick_duration)))

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
            floor.players.pop(player_id, None)
            connections.pop(player_id, None)
            log.info("Player %s saved and disconnected", player_id[:8])

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

            intents, intent_queue[:] = list(intent_queue), []
            result = engine.step(intents)

            if not connections:
                continue

            # Push authoritative player state every tick so the HUD stays live.
            for pid in list(connections.keys()):
                p = floor.players.get(pid)
                if p:
                    result.events.append({
                        "type": "player_update",
                        "player_id": pid,
                        "hp": p.hp,
                        "max_hp": p.max_hp,
                        "skills": {**p.skills.combat, **p.skills.non_combat},
                    })

            broadcast = json.dumps({
                "tick": result.tick,
                "tick_duration": result.tick_duration,
                "events": result.events,
            })
            await asyncio.gather(
                *(ws.send(broadcast) for ws in connections.values()),
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
