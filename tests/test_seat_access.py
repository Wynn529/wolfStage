import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.seat_config import MatchConfig, ModelProfile, SeatConfig
from app.seat_router import SeatRouter, ModelAttemptError
from app.human_service import HumanService, HumanInputError
from app.deepseek_client import DeepSeekClient


def config(human=False):
    profiles = (ModelProfile("a", "A", "deepseek", "model-a", "KEY_A"),
                ModelProfile("b", "B", "deepseek", "model-b", "KEY_B"))
    seats = tuple(SeatConfig(i, "human" if human and i == 10 else "ai",
                            None if human and i == 10 else ("a" if i < 6 else "b")) for i in range(1, 11))
    return MatchConfig("human" if human else "all_ai", seats, profiles)


def test_snapshot_roundtrip_and_repeated_models():
    c = config(True)
    assert MatchConfig.from_dict(json.loads(json.dumps(c.snapshot()))) == c
    assert c.seats[0].model_profile_id == c.seats[1].model_profile_id


@pytest.mark.parametrize("change", [
    {"seats": []}, {"mode": "bad"}, {"human_role": "god"},
    {"human_role": "wolf"}, {"profiles": []},
])
def test_invalid_config(change):
    value = config().snapshot()
    value.update(change)
    with pytest.raises(ValueError):
        MatchConfig.from_dict(value)


def test_route_isolated_context_and_metadata():
    calls = []
    class Client:
        def __init__(self, profile):
            self.profile = profile
        def complete(self, messages, **kwargs):
            calls.append((self.profile.model, json.loads(messages[1]["content"])))
            return {"action": "vote", "target": 2}, {"model_name": "actual", "input_tokens": 12}
    router = SeatRouter(config(), Client)
    context = {"agent_id": 1, "events": [{"text": "seat one only"}]}
    result = router.act(1, context=context, contract={}, prompt="rules", prompt_version="v2")
    router.act(9, context={"agent_id": 9, "events": []}, contract={}, prompt="rules", prompt_version="v2")
    assert calls[0][0] == "model-a" and calls[1] == ("model-b", {"agent_id": 9, "events": [], "action_contract": {}})
    assert "action_contract" not in context
    assert result.meta["input_tokens"] == 12 and result.meta["output_tokens"] is None
    assert result.meta["model_name"] == "actual"


def test_human_does_not_call_model():
    def forbidden(profile):
        pytest.fail("human called model")
    with pytest.raises(ValueError, match="waiting_human"):
        SeatRouter(config(True), forbidden).act(10, context={}, contract={}, prompt="", prompt_version="v")


def test_failure_no_fallback_or_secret_in_message():
    calls = []
    def broken(profile):
        calls.append(profile)
        raise RuntimeError("secret-key")
    with pytest.raises(ModelAttemptError) as failure:
        SeatRouter(config(), broken).act(1, context={"agent_id": 1}, contract={}, prompt="", prompt_version="v")
    assert len(calls) == 1
    assert "secret-key" not in str(failure.value)
    assert failure.value.meta["input_tokens"] is None


def setup_human(tmp_path):
    service = HumanService(tmp_path / "human.sqlite")
    token = service.bind("g", 10)
    service.publish("g", "p1", 20, contract={"allowed_actions": ["vote"]})
    return service, token


def submit(service, token, **changes):
    args = dict(operation_id="op1", pending_id="p1", expected_cursor=20,
                action={"action": "vote", "target": 2})
    args.update(changes)
    return service.submit("g", token, **args)


def test_persistent_queue_and_idempotency(tmp_path):
    service, token = setup_human(tmp_path)
    assert service.pending("g", token)["seat_id"] == 10
    assert submit(service, token) == submit(service, token)
    restored = HumanService(tmp_path / "human.sqlite")
    assert restored.pending("g", token)["status"] == "submitted"
    assert restored.queued("g")["action"] == {"action": "vote", "target": 2}
    restored.complete("g", "p1")
    assert restored.queued("g") is None
    restored.publish("g", "p2", 21, contract={})
    assert restored.pending("g", token)["pending_id"] == "p2"


@pytest.mark.parametrize("changes", [
    {"expected_cursor": 19}, {"pending_id": "old"},
    {"action": {"action": "vote", "target": 2, "reason": "made up"}},
    {"action": {"action": "vote", "player_id": 1}},
])
def test_reject_stale_and_identity_override(tmp_path, changes):
    service, token = setup_human(tmp_path)
    with pytest.raises(HumanInputError):
        submit(service, token, **changes)
    assert service.queued("g") is None


def test_session_bound_to_game(tmp_path):
    service, token = setup_human(tmp_path)
    other = service.bind("other", 1)
    with pytest.raises(PermissionError):
        service.pending("g", other)
    with pytest.raises(PermissionError):
        submit(service, other)


def test_concurrent_clicks_only_one_queued(tmp_path):
    service, token = setup_human(tmp_path)
    def click(i):
        try:
            return submit(service, token, operation_id=str(i))["status"]
        except HumanInputError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(click, range(4)))
    assert results.count("submitted") == 1
    assert results.count("conflict") == 3


def test_deepseek_response_model_and_unknown_usage(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"model": "actual-version", "choices": [
                {"message": {"content": '{"action":"vote","target":2}'}}]}).encode()
    def request(req, timeout):
        assert req.full_url == "https://example.test/v1/chat/completions"
        assert timeout == 12
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", request)
    client = DeepSeekClient(api_key="fake", timeout=12, base_url="https://example.test/v1/")
    action, meta = client.complete([], agent_id="1", prompt_version="v")
    assert action["target"] == 2
    assert meta["model_name"] == "actual-version"
    assert meta["input_tokens"] is None and meta["output_tokens"] is None


def test_operation_conflict_and_stale_publish(tmp_path):
    service, token = setup_human(tmp_path)
    submit(service, token)
    with pytest.raises(HumanInputError, match="operation conflict"):
        submit(service, token, action={"action": "vote", "target": 3})
    with pytest.raises(HumanInputError):
        service.publish("g", "p2", 21, contract={})
    service.complete("g", "p1")
    with pytest.raises(HumanInputError):
        service.publish("g", "p2", 20, contract={})
