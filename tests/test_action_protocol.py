"""真实代理协议回归，使用模拟客户端，不请求外部API。"""
import json

import pytest

from app.agents import DeepSeekAgent, action_contract, build_context
from app.engine import GameEngine
from app.rules import GameState


ROLES = {1: "wolf", 2: "wolf", 3: "seer", 4: "villager", 5: "villager", 6: "villager"}


class FakeClient:
    model = "test-model"

    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def complete(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        return next(self.actions), {}


def action(kind, target):
    return {"action": kind, "target": target, "text": "", "reason": "测试依据"}


@pytest.mark.parametrize("phase,actor,kind,targets", [
    ("night", 1, "kill", [3, 4, 5, 6]),
    ("night", 3, "inspect", [1, 2, 4, 5, 6]),
    ("day_speak", 1, "speak", [None]),
    ("day_speak", 3, "speak", [None]),
    ("day_speak", 4, "speak", [None]),
    ("day_vote", 1, "vote", [2, 3, 4, 5, 6]),
    ("day_vote", 4, "vote", [1, 2, 3, 5, 6]),
])
def test_actual_request_has_current_protocol(phase, actor, kind, targets):
    state = GameState("test", 42, ROLES, phase=phase)
    client = FakeClient([action(kind, targets[0])])
    context = build_context(state, [], actor, ROLES[actor])
    DeepSeekAgent(client).act(state, actor, context)
    messages, meta = client.requests[0]
    contract = json.loads(messages[1]["content"])["action_contract"]
    assert contract["allowed_actions"] == [kind]
    assert contract["allowed_targets"] == targets
    assert "wolf_kill" in messages[0]["content"]
    assert meta["prompt_version"].endswith("+action-v1")
    assert "action_contract" not in context


def test_request_keeps_other_private_events_hidden():
    state = GameState("test", 42, ROLES, phase="day_vote")
    events = [{"event_id": 1, "visibility": [3], "secret": "inspection"},
              {"event_id": 2, "visibility": "public", "text": "公开发言"}]
    context = build_context(state, events, 4, "villager")
    client = FakeClient([action("vote", 1)])
    DeepSeekAgent(client).act(state, 4, context)
    request = json.loads(client.requests[0][0][1]["content"])
    assert request["events"] == [events[1]]
    assert action_contract(state, 4)["allowed_targets"] == [1, 2, 3, 5, 6]


def test_invalid_action_retry_gets_feedback_and_records_success(tmp_path):
    client = FakeClient([action("wolf_kill", 4), action("kill", 4)])
    engine = GameEngine(tmp_path, agent=DeepSeekAgent(client))
    gid = engine.create_game(42, ROLES)["game_id"]
    result = engine.advance_one_event(gid)
    assert result["status"] == "running"
    assert len(client.requests) == 2
    first = json.loads(client.requests[0][0][1]["content"])
    retry = json.loads(client.requests[1][0][1]["content"])
    assert "validation_feedback" not in first
    assert retry["validation_feedback"]["previous_action"]["action"] == "wolf_kill"
    assert "not allowed" in retry["validation_feedback"]["error"]
    calls = engine.store.read_calls(gid)
    assert [c["success"] for c in calls] == [False, True]
    assert [c["attempt"] for c in calls] == [1, 2]
    assert calls[1]["validation_error"] is None
    assert all(c["prompt_version"] == "wolf-v1+action-v1" for c in calls)
    assert not any(e["type"] == "model_failure" for e in engine.store.read(gid))
    assert any(e["type"] == "wolf_proposal" and e["target"] == 4 for e in engine.store.read(gid))


def test_two_invalid_actions_still_fail_without_normalization(tmp_path):
    client = FakeClient([action("wolf_kill", 4), action("wolf_kill", 4)])
    engine = GameEngine(tmp_path, agent=DeepSeekAgent(client))
    gid = engine.create_game(42, ROLES)["game_id"]
    result = engine.advance_one_event(gid)
    assert result["status"] == "failed"
    assert len(client.requests) == 2
    assert all(not c["success"] for c in engine.store.read_calls(gid))
    assert not any(e["type"] == "wolf_proposal" for e in engine.store.read(gid))
    engine.advance_one_event(gid)
    assert len(client.requests) == 2
