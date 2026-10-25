from pathlib import Path

import pytest

from app.agents import build_context, visible_events
from app.engine import GameEngine
from app.rules import GameState, assign_roles, check_action, tally_votes, winner


def test_seed_reproducible():
    assert assign_roles(7) == assign_roles(7)


def test_role_config():
    roles = {1: "wolf", 2: "wolf", 3: "seer", 4: "villager", 5: "villager", 6: "villager"}
    assert assign_roles(1, roles) == roles


@pytest.fixture
def engine():
    return GameEngine(".test-runs")


def test_create_and_advance(engine):
    created = engine.create_game(42)
    result = engine.advance_one_event(created["game_id"])
    assert result["new_events"] == [2, 3]
    assert result["cursor"] == 3


def test_full_stub_game_finishes_or_limits(engine):
    gid = engine.create_game(42)["game_id"]
    for _ in range(200):
        result = engine.advance_one_event(gid)
        if result["status"] != "running":
            break
    assert result["status"] in {"finished", "incomplete", "failed"}


def test_advance_idempotent_when_paused(engine):
    gid = engine.create_game(1)["game_id"]
    engine.pause_game(gid)
    before = len(engine.store.read(gid))
    engine.advance_one_event(gid)
    assert len(engine.store.read(gid)) == before


def test_resume(engine):
    gid = engine.create_game(1)["game_id"]
    engine.pause_game(gid); engine.resume_game(gid)
    assert engine.advance_one_event(gid)["status"] == "running"


def test_visibility():
    events = [{"type": "secret", "visibility": [1]}, {"type": "public", "visibility": "public"}]
    assert len(visible_events(events, 1)) == 2
    assert len(visible_events(events, 2)) == 1


def test_context_wolf_teammates():
    state = GameState("x", 1, {1:"wolf",2:"wolf",3:"seer",4:"villager",5:"villager",6:"villager"})
    assert build_context(state, [], 1, "wolf")["wolf_teammates"] == [2]


def test_check_actions():
    state = GameState("x", 1, {1:"wolf",2:"wolf",3:"seer",4:"villager",5:"villager",6:"villager"})
    assert check_action(state, 1, {"action":"kill","target":4,"text":"x","reason":"y"}) is None
    assert check_action(state, 3, {"action":"kill","target":4,"text":"x","reason":"y"})
    assert check_action(state, 1, {"action":"kill","target":1,"text":"x","reason":"y"})


def test_tally():
    assert tally_votes({1: 2, 2: 2, 3: 4})[0] == 2
    assert tally_votes({1: 2, 2: 3})[0] is None
    assert tally_votes({})[0] is None


@pytest.mark.parametrize("alive, expected", [({1,2,3,4}, "wolves"), ({3,4,5,6}, "villagers"), ({1,2,3}, "wolves")])
def test_winner(alive, expected):
    state = GameState("x", 1, {1:"wolf",2:"wolf",3:"seer",4:"villager",5:"villager",6:"villager"}, alive=alive)
    assert winner(state) == expected


def test_logs_and_metrics(engine):
    gid = engine.create_game(2)["game_id"]
    engine.advance_one_event(gid)
    assert engine.store.read(gid)
    assert engine.get_metrics(gid)["event_count"] >= 2


def test_replay_read_only(engine):
    gid = engine.create_game(2)["game_id"]
    before = len(engine.store.read_calls(gid))
    engine.replay_game(gid)
    assert len(engine.store.read_calls(gid)) == before


def test_jsonl_paths(engine):
    gid = engine.create_game(2)["game_id"]
    engine.advance_one_event(gid)
    path = Path(engine.store.root) / gid
    assert (path / "events.jsonl").exists()
    assert (path / "summary.json").exists()


def test_restart_recovers_state(tmp_path=None):
    root = ".restart-runs"
    first = GameEngine(root)
    gid = first.create_game(9)["game_id"]
    first.advance_one_event(gid)
    second = GameEngine(root)
    assert gid in second.games
    assert second.games[gid].event_id == first.games[gid].event_id


def test_operation_id_is_idempotent(engine):
    gid = engine.create_game(12)["game_id"]
    first = engine.advance_one_event(gid, "op-1")
    second = engine.advance_one_event(gid, "op-1")
    assert first == second
    assert len(engine.store.read(gid)) == first["cursor"]
    conflict = engine.advance_one_event(gid, "op-2", expected_cursor=0)
    assert conflict["status"] == "conflict"
    assert engine.get_operation(gid, "op-1")["status"] == "completed"


def test_reason_is_not_public(engine):
    gid = engine.create_game(13)["game_id"]
    for _ in range(30):
        engine.advance_one_event(gid)
        events = engine.store.read(gid)
        speech = next((e for e in events if e["type"] == "public_speech"), None)
        if speech:
            public_view = engine.get_events(gid, view="4")
            assert "reason" not in speech
            assert all(e.get("type") != "agent_reason" or e.get("actor") == 4 for e in public_view)
            return
    raise AssertionError("stub game did not reach public speech")
