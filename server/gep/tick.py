"""The 1 Hz GEP tick loop (compendium §6). Deliberately generic: this module
knows nothing about combat, gathering, or movement -- systems register
handlers for intent types and queued-action kinds, and the engine just
dispatches. Adding new content is a new registration, never a new branch
here (§20's verb-vocabulary pattern applied to the loop itself).

Per-tick order: (1) advance tick counter, (2) drain inbound intents, (3)
execute due queued actions, (4) handlers mutate live state directly, (5)
caller broadcasts the returned TickResult. The counter advances before
intents are drained (a minor reordering of compendium §6's listed steps)
so that `schedule(delay_ticks=N, ...)` called from an intent handler means
exactly "N ticks after the tick this intent is being applied on" -- with the
literal drain-then-advance order, a handler running during tick 1 would see
`self.tick == 0` and every delay would land one tick early.
"""
from dataclasses import dataclass, field
from typing import Callable

from gep.queue import ActionQueue

IntentHandler = Callable[[dict, "TickEngine"], list[dict] | None]
ActionHandler = Callable[[dict, "TickEngine"], list[dict] | None]


@dataclass
class TickResult:
    tick: int
    tick_duration: float
    events: list[dict] = field(default_factory=list)


class TickEngine:
    def __init__(self, tick_duration: float = 1.0):
        self.tick = 0
        # Dilation is stubbed to a fixed duration for V1 (compendium §25);
        # the field is real in the protocol from day one so clients never
        # need a breaking change when dilation activates later.
        self.tick_duration = tick_duration
        self.queue = ActionQueue()
        self._intent_handlers: dict[str, IntentHandler] = {}
        self._action_handlers: dict[str, ActionHandler] = {}

    def register_intent_handler(self, intent_type: str, handler: IntentHandler) -> None:
        self._intent_handlers[intent_type] = handler

    def register_action_handler(self, kind: str, handler: ActionHandler) -> None:
        self._action_handlers[kind] = handler

    def schedule(self, delay_ticks: int, kind: str, payload: dict) -> None:
        self.queue.schedule(self.tick + delay_ticks, kind, payload)

    def step(self, intents: list[dict]) -> TickResult:
        events: list[dict] = []

        self.tick += 1

        for intent in intents:
            handler = self._intent_handlers.get(intent.get("intent_type"))
            if handler is None:
                events.append({"type": "error", "reason": f"unknown intent_type {intent.get('intent_type')!r}"})
                continue
            events.extend(handler(intent, self) or [])

        for action in self.queue.pop_due(self.tick):
            handler = self._action_handlers.get(action.kind)
            if handler is None:
                events.append({"type": "error", "reason": f"unknown action kind {action.kind!r}"})
                continue
            events.extend(handler(action.payload, self) or [])

        return TickResult(tick=self.tick, tick_duration=self.tick_duration, events=events)
