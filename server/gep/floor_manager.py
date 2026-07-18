"""Owns the set of active floors and which floor each player is on.

Floors are built on demand and cached. Extracted from the server so the
floor-transition logic (the tricky part of multi-floor travel) is unit
testable without a live WebSocket.
"""
from typing import Callable

from gep.floor_state import FloorState
from gep.tick import TickEngine

BuildFn = Callable[[int, Callable], tuple[FloorState, TickEngine]]


class FloorManager:
    def __init__(self, build_fn: BuildFn):
        # build_fn(floor_number, on_change_floor) -> (FloorState, TickEngine)
        self._build = build_fn
        self.floors: dict[int, tuple[FloorState, TickEngine]] = {}
        self.player_floor: dict[str, int] = {}
        self.pending_snapshots: set[str] = set()

    def get_or_build(self, floor_number: int) -> tuple[FloorState, TickEngine]:
        if floor_number not in self.floors:
            self.floors[floor_number] = self._build(floor_number, self.change_floor)
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
        """Move a player up/down one floor. Returns the new floor number, or
        None if the move is invalid (unknown player, or below floor 1).
        Arrival lands on the exit that leads back the way you came.
        """
        cur = self.player_floor.get(player_id)
        if cur is None:
            return None
        cur_floor = self.floors[cur][0]
        player = cur_floor.players.get(player_id)
        if player is None:
            return None

        target = cur + 1 if direction == "up" else cur - 1
        if target < 1:
            return None

        target_floor = self.get_or_build(target)[0]
        if direction == "up":
            arrival = target_floor.layout.down_exit or target_floor.layout.up_exit
        else:
            arrival = target_floor.layout.up_exit

        cur_floor.players.pop(player_id, None)
        target_floor.players[player_id] = player
        player.floor_number = target
        player.tile = arrival
        self.player_floor[player_id] = target
        self.pending_snapshots.add(player_id)
        return target
