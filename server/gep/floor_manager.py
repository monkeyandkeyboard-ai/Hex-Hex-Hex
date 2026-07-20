"""Owns the set of active floors and which floor each player is on.

Floors are built on demand and cached. Extracted from the server so the
floor-transition logic (the tricky part of multi-floor travel) is unit
testable without a live WebSocket.
"""
from typing import Callable

from gep.floor_state import FloorState
from gep.tick import TickEngine

BuildFn = Callable[[int, Callable, Callable], tuple[FloorState, TickEngine]]


class FloorManager:
    def __init__(self, build_fn: BuildFn):
        # build_fn(floor_number, on_change_floor, on_relocate)
        #     -> (FloorState, TickEngine)
        # Two movers, because they answer different questions: on_change_floor
        # takes a direction (stairs), on_relocate takes an absolute floor and
        # tile (respawn to an anchor).
        self._build = build_fn
        self.floors: dict[int, tuple[FloorState, TickEngine]] = {}
        self.player_floor: dict[str, int] = {}
        self.pending_snapshots: set[str] = set()

    def get_or_build(self, floor_number: int) -> tuple[FloorState, TickEngine]:
        if floor_number not in self.floors:
            self.floors[floor_number] = self._build(
                floor_number, self.change_floor, self.move_to_floor)
        return self.floors[floor_number]

    def add_player(self, player, floor_number: int = 1) -> FloorState:
        floor = self.get_or_build(floor_number)[0]
        floor.players[player.id] = player
        player.floor_number = floor_number
        self.player_floor[player.id] = floor_number
        return floor

    def remove_player(self, player_id: str) -> None:
        n = self.player_floor.pop(player_id, None)
        if n is not None and n in self.floors:
            self.floors[n][0].players.pop(player_id, None)

    def change_floor(self, player_id: str, direction: str) -> int | None:
        """Take the stairs one floor up or down. Returns the new floor number,
        or None if the move is invalid (unknown player, or below floor 1).

        Arrival lands on the exit that leads back the way you came: going up
        puts you on the new floor's down-stairs, going down puts you on its
        up-stairs. That reciprocity is what makes travel reversible -- step
        back onto the tile you arrived on and you return where you were.
        """
        cur = self.player_floor.get(player_id)
        if cur is None:
            return None

        target = cur + 1 if direction == "up" else cur - 1
        if target < 1:
            return None

        target_layout = self.get_or_build(target)[0].layout
        if direction == "up":
            # A floor always has an up exit but only floor >1 has a down exit;
            # the fallback matters only if the target is somehow floor 1.
            arrival = target_layout.down_exit or target_layout.up_exit
        else:
            arrival = target_layout.up_exit

        return self.move_to_floor(player_id, target, arrival)

    def move_to_floor(self, player_id: str, floor_number: int,
                      tile: tuple[int, int] | None = None) -> int | None:
        """Relocate a player to an arbitrary floor and tile.

        `change_floor` is the stairs case, which derives the destination tile
        from the exits. This is the general case, used by respawn to send a
        defeated player to an anchor that may be any floor -- an absolute
        destination, not a step in a direction. Keeping them separate is the
        point: respawn to floor 1 from floor 7 is one move, not six.

        The player object itself is carried between floors rather than
        rebuilt, so combat state, inventory, and skills survive the handoff.
        """
        cur = self.player_floor.get(player_id)
        if cur is None or floor_number < 1:
            return None
        cur_floor = self.floors[cur][0]
        player = cur_floor.players.get(player_id)
        if player is None:
            return None

        target_floor = self.get_or_build(floor_number)[0]

        cur_floor.players.pop(player_id, None)
        target_floor.players[player_id] = player
        player.floor_number = floor_number
        if tile is not None:
            player.tile = tile
        self.player_floor[player_id] = floor_number
        self.pending_snapshots.add(player_id)
        return floor_number
