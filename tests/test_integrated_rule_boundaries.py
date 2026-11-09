import pytest

from app.agents import StubAgent, action_contract
from app.engine import GameEngine
from app.events import EventStore
from app.rules import GameState, check_action

ROLES = {1:"wolf",2:"wolf",3:"wolf",4:"seer",5:"witch",6:"hunter",7:"villager",8:"villager",9:"villager",10:"villager"}


class SkillAgent(StubAgent):
    def act(self, state, actor, context):
        if state.phase == "hunter_action":
            return dict(action="shoot", target=1, text="", reason="test")
        if state.phase == "last_words":
            return dict(action="last_words", target=None, text="遗言测试", reason="test")
        return super().act(state, actor, context)


def test_hunter_ai_called_before_night_win_check(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.pending["kill_proposals"] = {1:6,2:6,3:6}
    e._resolve_night(s)
    assert s.phase == "hunter_action"
    e.advance_one_event(gid)
    assert 1 not in s.alive and s.hunter_processed
    assert e.store.read_calls(gid)[0]["agent_id"] == 6


def test_poison_overrides_knife_for_hunter(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.pending.update(kill_proposals={1:6},poison_target=6)
    e._resolve_night(s)
    assert s.death_reasons[6] == "witch_poison" and s.phase == "day_speak"
    assert not s.hunter_triggered


def test_exiled_hunter_shoots_then_speaks(tmp_path):
    e = GameEngine(str(tmp_path), agent=SkillAgent())
    gid = e.create_game(role_config=ROLES)["game_id"]
    s = e.games[gid]
    s.phase="day_vote";s.day=1;s.day_voted=set(ROLES);s.votes={1:6,2:6,3:6}
    e.advance_one_event(gid)
    assert s.phase == "hunter_action"
    e.advance_one_event(gid)
    assert s.phase == "last_words" and 1 not in s.alive
    e.advance_one_event(gid)
    assert any(v["type"]=="last_words" and v["text"]=="遗言测试" for v in e.store.read(gid))


def test_witch_second_night_self_save_rejected_and_contract_matches():
    s=GameState("g",1,ROLES,day=1,pending={"night_kill_target":5})
    assert check_action(s,5,dict(action="save",target=None))
    assert "save" not in action_contract(s,5)["allowed_actions"]
    assert {"pass","poison"} == set(action_contract(s,5)["allowed_actions"])


def test_offline_ten_player_game_terminates(tmp_path):
    e=GameEngine(str(tmp_path),agent=StubAgent())
    gid=e.create_game(seed=42)["game_id"]
    for i in range(300):
        result=e.advance_one_event(gid,str(i))
        if result["status"] in {"finished","failed","incomplete"}:
            break
    assert result["status"] == "finished"


@pytest.mark.parametrize("ballots, target, abstentions", [
    ({1: 6, 2: 6, 3: 7}, 6, 7),
    ({1: 6, 2: 7}, None, 8),
    ({}, None, 10),
])
def test_sealed_ballots_use_the_same_context_and_open_together(tmp_path, ballots, target, abstentions):
    contexts = []

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            contexts.append([event["event_id"] for event in context["events"]])
            choice = ballots.get(actor)
            return {"action": "vote" if choice else "pass", "target": choice}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    game_id = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[game_id]
    state.phase, state.day = "day_vote", 1
    for actor in ROLES:
        engine.advance_one_event(game_id, f"vote-{actor}")
        events = engine.get_events(game_id, view="god")
        assert not any(e["type"] in {"vote", "vote_tally", "exile"} for e in events)
        assert "target" not in events[-1]
    assert all(context == contexts[0] for context in contexts)

    engine = GameEngine(str(tmp_path))
    result = engine.advance_one_event(game_id, "open")
    assert engine.advance_one_event(game_id, "open") == result
    public = engine.get_events(game_id, view="public")
    tally, = [e for e in public if e["type"] == "vote_tally"]
    assert tally["counts"] == {str(p): list(ballots.values()).count(p) for p in ROLES}
    assert tally["abstentions"] == abstentions
    assert tally["target"] == target
    assert tally["resolution"] == ("exile" if target else "no_exile")
    assert [e["target"] for e in public if e["type"] == "exile"] == ([target] if target else [])


@pytest.mark.parametrize("stop_after", ["vote_commit.json", "events.jsonl", "state.json"])
def test_vote_commit_recovers_without_publishing_duplicate_events(tmp_path, monkeypatch, stop_after):
    engine = GameEngine(str(tmp_path), agent=StubAgent())
    game_id = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[game_id]
    state.phase, state.day = "day_vote", 1
    state.day_voted = set(ROLES)
    state.votes = {1: 6, 2: 6, 3: 7}
    engine.store.write_state(game_id, state.to_dict())
    cursor = state.event_id
    log = tmp_path / game_id / "events.jsonl"
    before = log.read_bytes()
    replace = EventStore._replace

    def stop_after_write(path, content):
        replace(path, content)
        if path.name == stop_after:
            raise OSError("simulated interruption")

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "_replace", staticmethod(stop_after_write))
        with pytest.raises(OSError, match="simulated interruption"):
            engine.advance_one_event(game_id, "settle", cursor)
    after_stop = log.read_bytes()

    restored = GameEngine(str(tmp_path), agent=StubAgent())
    operation = restored.get_operation(game_id, "settle")
    result = restored.advance_one_event(game_id, "settle", cursor)
    assert operation["status"] == "completed" and result == operation["result"]
    events = restored.store.read(game_id)
    assert [event["event_id"] for event in events] == list(range(1, cursor + 4))
    assert [event["type"] for event in events[-3:]] == ["vote_tally", "exile", "hunter_triggered"]
    assert events[-3]["counts"]["6"] == 2 and events[-3]["abstentions"] == 7
    assert result["phase"] == "hunter_action" and 6 not in restored.games[game_id].alive
    assert result["summary"]["event_count"] == len(events)
    assert log.read_bytes().startswith(before)
    if stop_after != "vote_commit.json":
        assert log.read_bytes() == after_stop
    assert not (tmp_path / game_id / "vote_commit.json").exists()


def test_vote_log_replacement_retries_a_windows_reader_lock(tmp_path, monkeypatch):
    from pathlib import Path

    engine = GameEngine(str(tmp_path), agent=StubAgent())
    game_id = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[game_id]
    state.phase, state.day, state.day_voted = "day_vote", 1, set(ROLES)
    state.votes = {1: 6, 2: 6}
    replace = Path.replace
    denied = 0

    def locked_log(path, target):
        nonlocal denied
        if target.name == "events.jsonl" and denied == 0:
            denied += 1
            error = PermissionError("simulated Windows sharing violation")
            error.winerror = 32
            raise error
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", locked_log)
    result = engine.advance_one_event(game_id, "settle")
    assert denied == 1 and result["phase"] == "hunter_action"
    assert sum(event["type"] == "vote_tally" for event in engine.store.read(game_id)) == 1


def test_model_timeout_retry_after_restart_does_not_bypass_cursor_or_validation(tmp_path):
    calls = []

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            if len(calls) < 3:
                raise TimeoutError("simulated timeout")
            return {"action": "vote", "target": actor}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    game_id = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[game_id]
    state.phase, state.day = "day_vote", 1
    state.day_voted, state.votes = {1}, {1: 3}
    first = engine.advance_one_event(game_id, "first")
    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    assert engine.get_status(game_id)["can_retry_model"]
    second = engine.advance_one_event(game_id, "second", first["cursor"], retry_model=True)
    assert second["status"] == "failed" and engine.get_status(game_id)["can_retry_model"]
    assert engine.advance_one_event(game_id, "stale", first["cursor"], retry_model=True)["status"] == "conflict"
    third = engine.advance_one_event(game_id, "third", second["cursor"], retry_model=True)
    assert third["status"] == "failed" and not engine.get_status(game_id)["can_retry_model"]
    assert engine.advance_one_event(game_id, "fourth", third["cursor"], retry_model=True)["status"] == "rejected"
    assert calls == [2, 2, 2]
    assert engine.games[game_id].votes == {1: 3}


@pytest.mark.parametrize("checkpoint", [
    "start_journal", "start_log", "start_state", "provider",
    "result_journal", "result_log", "result_state",
])
def test_model_retry_recovers_across_crash_checkpoints(tmp_path, monkeypatch, checkpoint):
    calls = []

    class Interrupted(BaseException):
        pass

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            if len(calls) == 1:
                raise TimeoutError("initial timeout")
            if checkpoint == "provider" and len(calls) == 2:
                raise Interrupted()
            return {"action": "vote", "target": 3}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    game_id = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[game_id]
    state.phase, state.day = "day_vote", 1
    state.day_voted, state.votes = {1}, {1: 3}
    failed = engine.advance_one_event(game_id, "initial")
    replace = EventStore._replace

    def interrupt_after_write(path, content):
        replace(path, content)
        kind = {"vote_commit.json": "journal", "events.jsonl": "log", "state.json": "state"}.get(path.name)
        if kind == "log":
            step = "result" if '"type": "vote_received"' in content else "start"
        else:
            step = "start" if '"model_retry_operation": "retry"' in content else "result"
        if checkpoint == f"{step}_{kind}":
            raise Interrupted()

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "_replace", staticmethod(interrupt_after_write))
        with pytest.raises(Interrupted):
            engine.advance_one_event(game_id, "retry", failed["cursor"], retry_model=True)

    calls_before_restart = list(calls)
    restored = GameEngine(str(tmp_path), agent=VotingAgent())
    first_recovery = restored.store.read(game_id)
    restored = GameEngine(str(tmp_path), agent=VotingAgent())
    assert restored.store.read(game_id) == first_recovery
    assert len(restored.store.read_calls(game_id)) == 2
    status = restored.get_status(game_id)
    result = restored.get_operation(game_id, "retry")["result"]
    assert calls == calls_before_restart and result is not None and not status["in_flight"]
    assert restored.advance_one_event(game_id, "retry", failed["cursor"], retry_model=True) == result
    assert calls == calls_before_restart
    if checkpoint.startswith("result_"):
        assert result["status"] == "running" and not status["can_retry_model"]
    else:
        assert result["error"] == "model_retry_interrupted" and status["can_retry_model"]
        assert restored.games[game_id].votes == {1: 3}
        continued = restored.advance_one_event(game_id, "manual-retry", status["cursor"], retry_model=True)
        assert continued["status"] == "running" and calls == calls_before_restart + [2]
    assert restored.games[game_id].votes == {1: 3, 2: 3}
    events = restored.store.read(game_id)
    assert [event["event_id"] for event in events] == list(range(1, len(events) + 1))
    assert sum(event["type"] == "vote_received" for event in events) == 1
    assert sum(event["type"] == "model_retry_interrupted" for event in events) == (
        0 if checkpoint.startswith("result_") else 1
    )


def test_interrupted_retry_consumes_budget_once(tmp_path):
    calls = []

    class Interrupted(BaseException):
        pass

    class TimeoutAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            if len(calls) == 1:
                raise TimeoutError("initial timeout")
            raise Interrupted()

    engine = GameEngine(str(tmp_path), agent=TimeoutAgent())
    engine.max_player_calls = 2
    gid = engine.create_game(role_config=ROLES)["game_id"]
    failed = engine.advance_one_event(gid, "initial")
    with pytest.raises(Interrupted):
        engine.advance_one_event(gid, "retry", failed["cursor"], retry_model=True)
    for _ in range(2):
        engine = GameEngine(str(tmp_path), agent=TimeoutAgent())
        engine.max_player_calls = 2
        status = engine.get_status(gid)
        assert not status["can_retry_model"]
        assert engine.advance_one_event(gid, "extra", status["cursor"], retry_model=True)["status"] == "rejected"
    assert calls == [1, 1]
    log = engine.store.read_calls(gid)
    assert len(log) == 2 and log[-1]["failure_stage"] == "interrupted"
    assert log[-1]["input_tokens"] is None


@pytest.mark.parametrize("human", [False, True])
def test_ballot_and_receipt_recover_as_one_operation(tmp_path, monkeypatch, human):
    calls = []

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            return {"action": "vote", "target": 3}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    gid = engine.create_game(role_config=ROLES, seat_controllers={2: "human"} if human else None)["game_id"]
    state = engine.games[gid]
    state.phase, state.day = "day_vote", 1
    state.day_voted, state.votes = {1}, {1: 3}
    pending = engine.advance_one_event(gid) if human else engine.get_status(gid)
    cursor = pending["cursor"]

    def submit(engine):
        if human:
            return engine.submit_human_action(gid, 2, {"action": "vote", "target": 3},
                                              action_id=pending["pending_action"]["action_id"],
                                              expected_cursor=cursor, operation_id="ballot")
        return engine.advance_one_event(gid, "ballot", cursor)

    replace = EventStore._replace

    def interrupt_after_receipt(path, content):
        replace(path, content)
        if path.name == "events.jsonl" and '"type": "vote_received"' in content:
            raise OSError("interrupted after receipt")

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "_replace", staticmethod(interrupt_after_receipt))
        with pytest.raises(OSError, match="interrupted after receipt"):
            submit(engine)
    restored = GameEngine(str(tmp_path), agent=VotingAgent())
    result = submit(restored)
    assert result["status"] == "running" and restored.games[gid].votes == {1: 3, 2: 3}
    assert calls == ([] if human else [2])
    events = restored.store.read(gid)
    assert [e["event_id"] for e in events] == list(range(1, len(events) + 1))
    assert sum(e["type"] == "vote_received" for e in events) == 1
    assert sum(e["type"] == "human_action_submitted" for e in events) == int(human)


def test_parallel_model_failures_keep_their_own_metadata(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock
    from app.seat_router import ModelAttemptError

    first_released, second_exited = Event(), Event()

    class Agent(StubAgent):
        def act(self, state, actor, context):
            self.last_metadata = {"http_status": 401} if state.game_id == first else {"request_timeout": True}
            error = ModelAttemptError(self.last_metadata)
            error.metadata = dict(self.last_metadata)
            raise error

    class OrderedLock:
        def __init__(self):
            self.lock = Lock()

        def __enter__(self):
            self.lock.acquire()

        def __exit__(self, exc_type, exc, tb):
            self.lock.release()
            if exc.metadata.get("http_status") == 401:
                first_released.set()
                assert second_exited.wait(5)
            else:
                second_exited.set()

    engine = GameEngine(str(tmp_path), agent=Agent())
    first, second = [engine.create_game(role_config=ROLES)["game_id"] for _ in range(2)]
    engine._agent_lock = OrderedLock()
    with ThreadPoolExecutor(2) as pool:
        left = pool.submit(engine.advance_one_event, first, "left")
        assert first_released.wait(5)
        right = pool.submit(engine.advance_one_event, second, "right")
        assert left.result(timeout=5)["status"] == right.result(timeout=5)["status"] == "failed"
    assert not engine.get_status(first)["can_retry_model"]
    assert engine.get_status(second)["can_retry_model"]
    assert engine.store.read_calls(first)[0]["http_status"] == 401


@pytest.mark.parametrize("stop_before", ["call_log", "ballot_commit"])
def test_saved_model_ballot_survives_restart_without_another_request(tmp_path, monkeypatch, stop_before):
    calls = []

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            return {"action": "vote", "target": 3 if len(calls) == 1 else 4}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    gid = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[gid]
    state.phase, state.day = "day_vote", 1
    state.day_voted, state.votes = {1}, {1: 3}
    cursor = state.event_id

    def interrupted(*args, **kwargs):
        raise OSError("simulated interruption")

    method = "append_call" if stop_before == "call_log" else "commit_events"
    monkeypatch.setattr(engine.store, method, interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        engine.advance_one_event(gid, "ballot", cursor)

    restored = GameEngine(str(tmp_path), agent=VotingAgent())
    assert restored.advance_one_event(gid, "ballot", cursor)["status"] == "running"
    assert restored.games[gid].votes == {1: 3, 2: 3}
    assert calls == [2] and len(restored.store.read_calls(gid)) == 1
    assert sum(e["type"] == "vote_received" for e in restored.store.read(gid)) == 1


def test_interrupted_ordinary_model_call_recovers_after_pause(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    entered, release = Event(), Event()

    class Interrupted(BaseException):
        pass

    class SlowAgent(StubAgent):
        def act(self, state, actor, context):
            entered.set()
            assert release.wait(5)
            raise Interrupted()

    engine = GameEngine(str(tmp_path), agent=SlowAgent())
    gid = engine.create_game(role_config=ROLES)["game_id"]
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(engine.advance_one_event, gid, "ordinary")
        try:
            assert entered.wait(5)
            assert engine.pause_game(gid)["pause_requested"]
        finally:
            release.set()
        with pytest.raises(Interrupted):
            future.result(timeout=5)

    restored = GameEngine(str(tmp_path), agent=StubAgent())
    status = restored.get_status(gid)
    assert status["status"] == "failed" and not status["in_flight"] and status["can_retry_model"]
    operation = restored.get_operation(gid, "ordinary")
    assert operation["status"] == "completed" and operation["result"]["error"] == "model_step_interrupted"
    assert restored.advance_one_event(gid, "ordinary") == operation["result"]
    assert len(restored.store.read_calls(gid)) == 1
    assert restored.store.read_calls(gid)[0]["failure_stage"] == "interrupted"
    result = restored.advance_one_event(gid, "manual", status["cursor"], retry_model=True)
    assert result["status"] == "running" and restored.games[gid].night_done == {1}


def test_waiting_human_event_and_pending_action_recover_together(tmp_path, monkeypatch):
    engine = GameEngine(str(tmp_path), agent=StubAgent())
    gid = engine.create_game(role_config=ROLES, seat_controllers={2: "human"})["game_id"]
    state = engine.games[gid]
    state.phase, state.day, state.day_voted = "day_vote", 1, {1}
    cursor = state.event_id
    replace = EventStore._replace

    def stop_after_waiting_event(path, content):
        replace(path, content)
        if path.name == "events.jsonl" and '"type": "waiting_human"' in content:
            raise OSError("interrupted after waiting event")

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "_replace", staticmethod(stop_after_waiting_event))
        with pytest.raises(OSError, match="interrupted after waiting event"):
            engine.advance_one_event(gid, "wait", cursor)
    restored = GameEngine(str(tmp_path), agent=StubAgent())
    result = restored.advance_one_event(gid, "wait", cursor)
    waiting, = [e for e in restored.store.read(gid) if e["type"] == "waiting_human"]
    assert result["status"] == "waiting_human" and result["pending_action"]["action_id"] == waiting["action_id"]
    assert waiting["event_id"] == cursor + 1 and restored.store.read_calls(gid) == []


def test_saved_manual_retry_ballot_recovers_without_another_request(tmp_path, monkeypatch):
    calls = []

    class VotingAgent(StubAgent):
        def act(self, state, actor, context):
            calls.append(actor)
            if len(calls) == 1:
                raise TimeoutError("initial timeout")
            return {"action": "vote", "target": 3 if len(calls) == 2 else 4}

    engine = GameEngine(str(tmp_path), agent=VotingAgent())
    gid = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[gid]
    state.phase, state.day = "day_vote", 1
    state.day_voted, state.votes = {1}, {1: 3}
    first = engine.advance_one_event(gid, "initial")
    commit = engine.store.commit_events

    def stop_before_ballot(game_id, state, events, summary=None):
        if any(event["type"] == "vote_received" for event in events):
            raise OSError("interrupted before ballot commit")
        commit(game_id, state, events, summary)

    monkeypatch.setattr(engine.store, "commit_events", stop_before_ballot)
    with pytest.raises(OSError, match="interrupted before ballot commit"):
        engine.advance_one_event(gid, "retry", first["cursor"], retry_model=True)
    restored = GameEngine(str(tmp_path), agent=VotingAgent())
    assert restored.advance_one_event(gid, "retry", first["cursor"], retry_model=True)["status"] == "running"
    assert restored.games[gid].votes == {1: 3, 2: 3}
    assert calls == [2, 2] and len(restored.store.read_calls(gid)) == 2
    events = restored.store.read(gid)
    assert [e["type"] for e in events][-2:] == ["model_retry_started", "vote_received"]


@pytest.mark.parametrize("classifier_fails", [False, True])
def test_speech_recovers_saved_classification_once(tmp_path, monkeypatch, classifier_fails):
    player_calls, small_calls = [], []

    class SpeakingAgent(StubAgent):
        def act(self, state, actor, context):
            player_calls.append(actor)
            return {"action": "speak", "text": "test speech"}

    def classifier(game_id, event_id, actor, text):
        small_calls.append(event_id)
        if classifier_fails:
            raise RuntimeError("test classifier failure")
        return {"intent": "discussion", "model_version": "test"}

    engine = GameEngine(str(tmp_path), agent=SpeakingAgent(), classifier=classifier)
    gid = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[gid]
    state.phase, state.day = "day_speak", 1
    commit = engine.store.commit_events

    def stop_before_speech(game_id, state, events, summary=None):
        if any(event["type"] == "public_speech" for event in events):
            raise OSError("interrupted before speech commit")
        commit(game_id, state, events, summary)

    monkeypatch.setattr(engine.store, "commit_events", stop_before_speech)
    with pytest.raises(OSError, match="interrupted before speech commit"):
        engine.advance_one_event(gid, "speech")
    restored = GameEngine(str(tmp_path), agent=SpeakingAgent(), classifier=classifier)
    result = restored.get_operation(gid, "speech")["result"]
    assert result["status"] == ("failed" if classifier_fails else "running")
    assert player_calls == [1] and len(small_calls) == 1
    log = [c for c in restored.store.read_calls(gid) if c["caller_type"] == "small_model"]
    assert len(log) == 1 and log[0]["success"] is not classifier_fails
    events = restored.store.read(gid)
    classification = "utterance_classification_failed" if classifier_fails else "utterance_classification"
    assert [e["type"] for e in events][-2:] == ["public_speech", classification]


@pytest.mark.parametrize("action, event_type, expected", [
    ("pause_game", "game_paused", "paused"), ("resume_game", "game_resumed", "running"),
])
def test_control_event_and_state_recover_together(tmp_path, monkeypatch, action, event_type, expected):
    engine = GameEngine(str(tmp_path), agent=StubAgent())
    gid = engine.create_game(role_config=ROLES)["game_id"]
    if action == "resume_game":
        engine.pause_game(gid)
    replace = EventStore._replace

    def stop_after_event(path, content):
        replace(path, content)
        if path.name == "events.jsonl" and f'"type": "{event_type}"' in content:
            raise OSError("interrupted after control event")

    with monkeypatch.context() as patch:
        patch.setattr(EventStore, "_replace", staticmethod(stop_after_event))
        with pytest.raises(OSError, match="interrupted after control event"):
            getattr(engine, action)(gid)
    restored = GameEngine(str(tmp_path), agent=StubAgent())
    assert restored.get_status(gid)["status"] == expected
    assert getattr(restored, action)(gid)["status"] == expected
    events = restored.store.read(gid)
    assert sum(e["type"] == event_type for e in events) == 1
    assert [e["event_id"] for e in events] == list(range(1, len(events) + 1))


def test_unicode_separators_survive_event_replay_and_restart(tmp_path):
    from app.ui_helpers import read_events
    from app.ui_metrics import read_calls

    text = "First\u2028second\u2029third\u0085fourth\nfifth"

    class SpeakingAgent(StubAgent):
        def act(self, state, actor, context):
            self.last_metadata = {"request_evidence": {"note": text}}
            return {"action": "speak", "text": text, "target": None}

    engine = GameEngine(tmp_path, agent=SpeakingAgent())
    gid = engine.create_game(role_config=ROLES)["game_id"]
    state = engine.games[gid]
    state.phase, state.day = "day_speak", 1
    assert engine.advance_one_event(gid, "first")["status"] == "running"
    speech, = [event for event in engine.get_events(gid) if event["type"] == "public_speech"]
    assert speech["text"] == text
    assert read_events(tmp_path, gid)[-1]["text"] == text
    assert read_calls(tmp_path, gid)[0]["request_evidence"]["note"] == text
    log = tmp_path / gid / "events.jsonl"
    before = log.read_bytes()

    restored = GameEngine(tmp_path, agent=SpeakingAgent())
    assert gid not in restored.unavailable_games
    assert restored.advance_one_event(gid, "second")["status"] == "running"
    assert [event["text"] for event in restored.store.read(gid) if event["type"] == "public_speech"] == [text, text]
    assert [call["request_evidence"]["note"] for call in restored.store.read_calls(gid)] == [text, text]
    assert log.read_bytes().startswith(before)
