"""Deterministic floor generation (compendium §4.1, §11.1). Mirrors
client/src/floorgen.js exactly -- same inputs must produce the same layout
on both sides, verified by server/tests/test_floorgen_golden.py.
"""
from dataclasses import dataclass, field

from gep.hexgrid import ring_tiles, tiles_in_radius
from gep.prng import Mulberry32, rng_for_tile, seed_from_floor
from gep.rolls import weighted_choice

Tile = tuple[int, int]


@dataclass
class FloorLayout:
    tower_id: str
    floor_number: int
    radius: int
    tiles: list[Tile]
    up_exit: Tile
    down_exit: Tile | None
    resource_nodes: dict[Tile, str] = field(default_factory=dict)
    monster_spawns: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tower_id": self.tower_id,
            "floor_number": self.floor_number,
            "radius": self.radius,
            "up_exit": list(self.up_exit),
            "down_exit": list(self.down_exit) if self.down_exit else None,
            "resource_nodes": {f"{q},{r}": rid for (q, r), rid in self.resource_nodes.items()},
            "monster_spawns": [
                {"tile": list(s["tile"]), "template_id": s["template_id"]}
                for s in self.monster_spawns
            ],
        }


def _floor_index(rng: Mulberry32, n: int) -> int:
    return min(n - 1, int(rng.next_float() * n))


def generate_floor(
    tower_id: str, floor_number: int, global_seed: str, ruleset: dict
) -> FloorLayout:
    floor_seed = seed_from_floor(tower_id, floor_number, global_seed)
    floor_rng = Mulberry32(floor_seed)

    radius = ruleset["radius"]
    all_tiles = tiles_in_radius(radius)
    ring_pool = ring_tiles(radius, radius)

    up_idx = _floor_index(floor_rng, len(ring_pool))
    up_exit = ring_pool.pop(up_idx)

    down_exit = None
    if floor_number > 1:
        down_idx = _floor_index(floor_rng, len(ring_pool))
        down_exit = ring_pool.pop(down_idx)

    reserved = {up_exit}
    if down_exit:
        reserved.add(down_exit)

    resource_nodes: dict[Tile, str] = {}
    resource_chance = ruleset["resource_spawn_chance"]
    resource_weights = ruleset["resource_weights"]
    for tile in all_tiles:
        if tile in reserved:
            continue
        q, r = tile
        tile_rng = rng_for_tile(floor_seed, q, r)
        if tile_rng.next_float() < resource_chance:
            resource_nodes[tile] = weighted_choice(tile_rng, resource_weights)

    available = [t for t in all_tiles if t not in reserved and t not in resource_nodes]
    spawn_pool = list(available)
    monster_spawns: list[dict] = []
    spawn_count = min(ruleset["monster_spawn_count"], len(spawn_pool))
    for _ in range(spawn_count):
        idx = _floor_index(floor_rng, len(spawn_pool))
        tile = spawn_pool.pop(idx)
        template_id = weighted_choice(floor_rng, ruleset["monster_weights"])
        monster_spawns.append({"tile": tile, "template_id": template_id})

    return FloorLayout(
        tower_id=tower_id,
        floor_number=floor_number,
        radius=radius,
        tiles=all_tiles,
        up_exit=up_exit,
        down_exit=down_exit,
        resource_nodes=resource_nodes,
        monster_spawns=monster_spawns,
    )
