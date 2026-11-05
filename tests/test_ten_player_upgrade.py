from app.engine import GameEngine
from app.rules import GameState, assign_roles, check_action


def test_ten_role_counts_and_determinism():
    first = assign_roles(7)
    assert first == assign_roles(7)
    assert len(first) == 10
    assert {role: list(first.values()).count(role) for role in set(first.values())} == {
        "wolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 4
    }


def test_human_seat_waits_and_submits(tmp_path):
    roles = {1: "wolf", 2: "wolf", 3: "wolf", 4: "seer", 5: "witch", 6: "hunter", 7: "villager", 8: "villager", 9: "villager", 10: "villager"}
    engine = GameEngine(str(tmp_path), agent=None)
    created = engine.create_game(1, roles, seat_controllers={1: "human"})
    result = engine.advance_one_event(created["game_id"], "first", expected_cursor=1)
    assert result["status"] == "waiting_human"
    pending = result["pending_action"]
    submitted = engine.submit_human_action(created["game_id"], 1, {"action": "kill", "target": 7, "text": "", "reason": ""}, action_id=pending["action_id"], expected_cursor=result["cursor"])
    assert submitted["status"] in {"running", "waiting_human"}
    assert all(c.get("agent_id") != 1 for c in engine.store.read_calls(created["game_id"]))


def test_witch_and_seer_constraints():
    roles = {1: "wolf", 2: "wolf", 3: "wolf", 4: "seer", 5: "witch", 6: "hunter", 7: "villager", 8: "villager", 9: "villager", 10: "villager"}
    state = GameState("g", 1, roles)
    assert check_action(state, 5, {"action": "save", "target": None}) is None
    state.witch_save_available = False
    assert check_action(state, 5, {"action": "save", "target": None})
    assert check_action(state, 4, {"action": "inspect", "target": 5}) is None
    state.inspection_history.add(5)
    assert check_action(state, 4, {"action": "inspect", "target": 5})


def test_human_action_is_not_model_call(tmp_path):
    roles = {1: "wolf", 2: "wolf", 3: "wolf", 4: "seer", 5: "witch", 6: "hunter", 7: "villager", 8: "villager", 9: "villager", 10: "villager"}
    engine = GameEngine(str(tmp_path))
    created = engine.create_game(1, roles, seat_controllers={1: "human"})
    result = engine.advance_one_event(created["game_id"], expected_cursor=1)
    pending = result["pending_action"]
    engine.submit_human_action(created["game_id"], 1, {"action": "kill", "target": 7, "text": "", "reason": ""}, action_id=pending["action_id"], expected_cursor=result["cursor"])
    assert all(call.get("agent_id") != 1 for call in engine.store.read_calls(created["game_id"]))
