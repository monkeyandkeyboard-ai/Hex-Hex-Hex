from gep.tick import TickEngine


def test_tick_advances_and_reports_duration():
    engine = TickEngine(tick_duration=1.0)
    result = engine.step([])
    assert result.tick == 1
    assert result.tick_duration == 1.0
    assert result.events == []


def test_intent_dispatches_to_registered_handler():
    engine = TickEngine()
    seen = []

    def handler(intent, eng):
        seen.append(intent)
        return [{"type": "ack", "id": intent["id"]}]

    engine.register_intent_handler("move-to-tile", handler)
    result = engine.step([{"intent_type": "move-to-tile", "id": "p1"}])

    assert seen == [{"intent_type": "move-to-tile", "id": "p1"}]
    assert result.events == [{"type": "ack", "id": "p1"}]


def test_unknown_intent_type_produces_error_event_not_crash():
    engine = TickEngine()
    result = engine.step([{"intent_type": "teleport-anywhere"}])
    assert result.events[0]["type"] == "error"


def test_queued_action_fires_on_exact_target_tick_not_before():
    engine = TickEngine()
    fired = []
    engine.register_action_handler("gather-complete", lambda payload, eng: fired.append(eng.tick))

    engine.schedule(delay_ticks=3, kind="gather-complete", payload={})

    for _ in range(2):
        engine.step([])
    assert fired == []  # not due yet at tick 2

    engine.step([])  # tick 3 -- now due
    assert fired == [3]


def test_action_never_re_executed_after_firing():
    engine = TickEngine()
    call_count = [0]
    engine.register_action_handler("weapon-cooldown", lambda payload, eng: call_count.__setitem__(0, call_count[0] + 1))
    engine.schedule(delay_ticks=1, kind="weapon-cooldown", payload={})

    for _ in range(5):
        engine.step([])

    assert call_count[0] == 1


def test_handler_can_schedule_followup_action_same_tick_executes_next():
    engine = TickEngine()
    log = []

    def start_travel(intent, eng):
        eng.schedule(delay_ticks=2, kind="arrive", payload={"who": intent["who"]})
        log.append("started")

    def arrive(payload, eng):
        log.append(f"arrived:{payload['who']}")

    engine.register_intent_handler("travel", start_travel)
    engine.register_action_handler("arrive", arrive)

    engine.step([{"intent_type": "travel", "who": "p1"}])  # tick 1: schedules arrival 2 ticks later, at tick 3
    engine.step([])  # tick 2
    assert log == ["started"]
    engine.step([])  # tick 3: arrival fires
    assert log == ["started", "arrived:p1"]


def test_queued_action_delay_is_relative_to_the_tick_it_was_scheduled_on():
    engine = TickEngine()
    fired_at = []
    engine.register_action_handler("done", lambda payload, eng: fired_at.append(eng.tick))

    def start(intent, eng):
        assert eng.tick == 1  # tick has already advanced by the time intents are handled
        eng.schedule(delay_ticks=1, kind="done", payload={})

    engine.register_intent_handler("go", start)
    engine.step([{"intent_type": "go"}])  # tick 1: schedules for tick 2
    assert fired_at == []
    engine.step([])  # tick 2: fires
    assert fired_at == [2]
