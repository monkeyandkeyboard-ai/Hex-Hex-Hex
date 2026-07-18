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
    # Starts from layout.resource_nodes; nodes removed on harvest, re-added on respawn.
    resource_nodes: dict[Tile, str] = field(default_factory=dict)

    # tile -> tick at which it respawns (for dormancy catch-up; §7)
    depleted_nodes: dict[Tile, int] = field(default_factory=dict)

    @classmethod
    def from_layout(cls, layout: FloorLayout) -> "FloorState":
        return cls(
            layout=layout,
            resource_nodes=dict(layout.resource_nodes),
        )

    @property
    def tile_set(self) -> set[Tile]:
        return set(self.layout.tiles)

    def is_valid_tile(self, tile: Tile) -> bool:
        return tile in self.tile_set

    def is_passable(self, tile: Tile) -> bool:
        """A tile is passable if it exists on the floor and is not occupied
        by a blocking entity. Resource nodes are not blocking (player walks
        onto the same tile to gather). Monsters block movement.
        """
        if not self.is_valid_tile(tile):
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
