"""Live mutable state for one active floor. Holds the generated layout plus
all entity positions and transient world state (depleted nodes, etc.).
This is what the GEP worker keeps in memory while the floor is active.
"""
from dataclasses import dataclass, field

from gep.entities import Monster, Player
from gep.floorgen import FloorLayout

Tile = tuple[int, int]


@dataclass
class FloorState:
    layout: FloorLayout

    players: dict[str, Player] = field(default_factory=dict)
    monsters: dict[str, Monster] = field(default_factory=dict)

    # Tiles with an active resource node (tile -> resource_id).
    # Seeded from the spawner's SpawnPlan at construction; nodes removed on
    # harvest, re-added on respawn.
    resource_nodes: dict[Tile, str] = field(default_factory=dict)

    # tile -> tick at which it respawns (for dormancy catch-up; §7)
    depleted_nodes: dict[Tile, int] = field(default_factory=dict)

    @classmethod
    def from_layout(cls, layout: FloorLayout, resource_nodes: dict[Tile, str] | None = None) -> "FloorState":
        """Build live state from a physical layout plus the spawner's output.

        `resource_nodes` comes from gep/spawner.py, not from the layout --
        map generation carries no entity data. Omitted for floors with
        nothing to gather (e.g. tests that only need tiles/exits).
        """
        return cls(
            layout=layout,
            resource_nodes=dict(resource_nodes) if resource_nodes else {},
        )

    @property
    def tile_set(self) -> frozenset[Tile]:
        """The floor's tiles as a set, built once and reused.

        This sits in A*'s inner loop by way of is_passable, so rebuilding it
        per call meant re-materialising ~12.5k tuples for every neighbour of
        every expanded node. Cached against the layout object rather than a
        plain attribute so that swapping the layout can't serve a stale set.
        """
        cached = getattr(self, "_tile_set_cache", None)
        if cached is None or cached[0] is not self.layout:
            cached = (self.layout, frozenset(self.layout.tiles))
            self._tile_set_cache = cached
        return cached[1]

    def is_valid_tile(self, tile: Tile) -> bool:
        return tile in self.tile_set

    def is_passable(self, tile: Tile) -> bool:
        """A tile is passable if it exists on the floor, terrain permits entry,
        and it is not occupied by a blocking entity. Resource nodes are not
        blocking (player walks onto the same tile to gather). Monsters block
        movement.

        This is the single gate both player movement (gep/systems/movement.py)
        and monster pursuit (gep/systems/monster_ai.py) route through, which is
        why the terrain check goes here rather than at either call site --
        terrain that stopped players but not monsters would let a goblin swim.
        """
        if not self.is_valid_tile(tile):
            return False
        if tile in self.layout.blocked:
            return False
        for monster in self.monsters.values():
            if monster.tile == tile and monster.alive:
                return False
        return True

    def player_at(self, tile: Tile) -> Player | None:
        for p in self.players.values():
            if p.tile == tile:
                return p
        return None

    def monster_at(self, tile: Tile) -> Monster | None:
        for m in self.monsters.values():
            if m.tile == tile and m.alive:
                return m
        return None
