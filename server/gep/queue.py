"""The one delayed-action mechanism (compendium §6): travel, gathering,
crafting, weapon cooldowns are all queue entries with a precomputed
Target_Tick, checked for arrival -- never re-simulated per tick.
"""
import heapq
import itertools
from dataclasses import dataclass, field


@dataclass(order=True)
class QueuedAction:
    target_tick: int
    seq: int
    kind: str = field(compare=False)
    payload: dict = field(compare=False)


class ActionQueue:
    def __init__(self):
        self._heap: list[QueuedAction] = []
        self._seq = itertools.count()

    def schedule(self, target_tick: int, kind: str, payload: dict) -> None:
        heapq.heappush(self._heap, QueuedAction(target_tick, next(self._seq), kind, payload))

    def pop_due(self, current_tick: int) -> list[QueuedAction]:
        due = []
        while self._heap and self._heap[0].target_tick <= current_tick:
            due.append(heapq.heappop(self._heap))
        return due

    def __len__(self) -> int:
        return len(self._heap)
