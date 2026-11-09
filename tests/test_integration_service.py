import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from app.agents import StubAgent
from app.engine import GameEngine
from app.integration_service import create_app
from app.provider_client import ProviderClient
from app.seat_config import MatchConfig

ROOT = Path(__file__).resolve().parents[1]


def request(app, method, path, body=None, token=None):
    """In-process ASGI request; no sockets, paid calls or extra HTTP dependencies."""
    async def run():
        parts = urlsplit(path)
        sent = []
        consumed = False
        async def receive():
            nonlocal consumed
            if consumed:
                await asyncio.sleep(0)
                return {"type": "http.disconnect"}
            consumed = True
            return {"type": "http.request", "body": json.dumps(body or {}).encode(), "more_body": False}
        async def send(message):
            sent.append(message)
        headers = [(b"content-type", b"application/json")]
        if token:
            headers.append((b"authorization", ("Bearer " + token).encode()))
        await app({"type": "http", "http_version": "1.1", "method": method, "scheme": "http", "path": parts.path,
                   "raw_path": parts.path.encode(), "query_string": parts.query.encode(), "headers": headers,
                   "server": ("test", 80), "client": ("test", 1), "root_path": ""}, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, json.loads(data)
    return asyncio.run(run())


def payload(human=True):
    config = json.loads((ROOT / "config/nine_models.example.json").read_text(encoding="utf-8"))
    config.pop("profiles")
    if not human:
        config["mode"] = "all_ai"
        config["seats"][-1].update(control_type="ai", model_profile_id="mimo")
    return config


@pytest.mark.parametrize("seed", ["42", True, 4.2, []])
def test_invalid_seed_is_rejected_before_creating_game(tmp_path, seed):
    app = create_app(tmp_path)
    code, result = request(app, "POST", "/games", dict(payload(False), seed=seed))
    assert code == 422 and result["detail"] == "seed must be an integer or null"
    assert app.state.engine.games == {}
    assert app.state.engine.store.list_games() == []


def test_invalid_advance_controls_are_rejected_before_model_call(tmp_path):
    app = create_app(tmp_path)
    _, created = request(app, "POST", "/games", dict(payload(False), seed=42))
    gid = created["game_id"]
    engine = app.state.engine
    engine.configured_agent = StubAgent()
    path = f"/games/{gid}/advance"
    for invalid in ({"operation_id": ["invalid"]}, {"operation_id": ""},
                    {"expected_cursor": True}, {"expected_cursor": 1.0},
                    {"expected_cursor": "1"}, {"expected_cursor": -1}):
        body = {"operation_id": "probe", "expected_cursor": 1, **invalid}
        assert request(app, "POST", path, body)[0] == 422
        assert engine.get_status(gid)["cursor"] == 1
        assert engine.store.read_calls(gid) == []
    code, result = request(app, "POST", path, {"operation_id": "valid", "expected_cursor": 1})
    assert code == 200 and result["cursor"] > 1 and len(engine.store.read_calls(gid)) == 1


def test_session_privacy_and_restart(tmp_path):
    app = create_app(tmp_path)
    status, created = request(app, "POST", "/games", payload())
    assert status == 200
    gid, token = created["game_id"], created["human_token"]
    app.state.engine._event(app.state.engine.games[gid], "private_probe", [1], secret="hidden-test")
    for path in ("status", "events", "metrics", "replay", "operations/unknown"):
        assert request(app, "GET", f"/games/{gid}/{path}")[0] == 403
    status, visible = request(app, "GET", f"/games/{gid}/status", token=token)
    assert status == 200 and visible["player_context"]["agent_id"] == 10
    assert "roles" not in visible["player_context"]
    assert all(e["type"] != "game_created" for e in visible["player_context"]["events"])
    assert all(e["type"] != "private_probe" for e in visible["player_context"]["events"])
    assert request(app, "GET", f"/games/{gid}/events?view=god&player_id=1", token=token)[0] == 403
    assert all(e["type"] != "private_probe" for e in request(app, "GET", f"/games/{gid}/events?view=god", token=token)[1])
    assert request(app, "POST", f"/games/{gid}/players/1/action", {}, token)[0] == 403
    restored = create_app(tmp_path)
    assert request(restored, "GET", f"/games/{gid}/status", token=token)[0] == 200
    assert request(restored, "GET", f"/games/{gid}/status", token="wrong")[0] == 403
    restored.state.engine.games[gid].status = "finished"
    assert request(restored, "GET", f"/games/{gid}/replay")[0] == 200


def test_nine_model_configs_drive_engine_calls(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    _, created = request(app, "POST", "/games", payload(False))
    engine, gid = app.state.engine, created["game_id"]
    state = engine.games[gid]
    state.phase = "day_vote"
    seen = []
    def send(self, req):
        body = json.loads(req.data)
        key = "input" if self.profile.api_protocol == "responses" else "messages"
        context = json.loads(body[key][1]["content"])
        seat = context["agent_id"]
        seen.append((seat, self.profile.model))
        text = json.dumps({"action": "vote", "target": 2 if seat != 2 else 1, "text": "", "reason": "test"})
        if key == "input":
            return {"model": self.model, "status": "completed", "usage": {"input_tokens": 3, "output_tokens": 4},
                    "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}
        return {"model": self.model, "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                "choices": [{"finish_reason": "stop", "message": {"content": text}}]}
    monkeypatch.setattr(ProviderClient, "_send", send)
    for config in state.seat_model_configs.values():
        monkeypatch.setenv(config["api_key_env"], "fake-key")
    for seat in range(1, 11):
        result = engine.advance_one_event(gid, f"vote-{seat}")
        assert result["status"] == "running"
    assert len(seen) == 10 and len({model for _, model in seen}) == 9
    calls = engine.store.read_calls(gid)
    assert all(c["success"] and c["input_tokens"] == 3 and c["provider"] for c in calls)
    restored = create_app(tmp_path)
    assert restored.state.engine.games[gid].seat_model_configs == state.seat_model_configs


def test_human_replay_after_day_limit_requires_original_session(tmp_path):
    app = create_app(tmp_path)
    _, created = request(app, "POST", "/games", payload())
    _, other = request(app, "POST", "/games", payload())
    gid, token = created["game_id"], created["human_token"]
    engine = app.state.engine
    state = engine.games[gid]
    state.phase, state.day, state.day_voted = "day_vote", 8, set(state.alive)
    path = f"/games/{gid}"
    assert request(app, "GET", path + "/replay", token=token)[0] == 403
    _, result = request(app, "POST", path + "/advance", {"operation_id": "day-limit"}, token)
    assert result["status"] == "incomplete"
    assert request(app, "GET", path + "/replay")[0] == 403
    assert request(app, "GET", path + "/replay", token=other["human_token"])[0] == 403
    code, replay = request(app, "GET", path + "/replay", token=token)
    assert code == 200 and replay[-1]["type"] == "game_limit"
    assert engine.store.read_calls(gid) == []


def test_human_submit_idempotent_and_no_model_call(tmp_path):
    app = create_app(tmp_path)
    _, created = request(app, "POST", "/games", payload())
    engine, gid, token = app.state.engine, created["game_id"], created["human_token"]
    state = engine.games[gid]
    state.phase = "day_vote"
    state.day_voted = set(range(1, 10))
    pending = engine.advance_one_event(gid)
    assert pending["status"] == "waiting_human"
    body = {"action_id": pending["pending_action"]["action_id"], "expected_cursor": pending["cursor"],
            "operation_id": "human-vote", "action": {"action": "vote", "target": 2, "text": ""}}
    path = f"/games/{gid}/players/10/action"
    status, first = request(app, "POST", path, body, token)
    assert status == 200
    assert request(app, "POST", path, body, token)[1] == first
    events = engine.get_events(gid, view="public")
    receipts = [e for e in events if e["type"] == "vote_received"]
    assert len(receipts) == 1 and "actor" not in receipts[0] and "target" not in receipts[0]
    assert not any(e["type"] in {"vote", "vote_tally"} for e in events)
    assert engine.store.read_calls(gid) == []
    body["action"]["reason"] = "fake"
    assert request(app, "POST", path, body, token)[0] == 422
    engine.advance_one_event(gid)
    tally = next(e for e in engine.get_events(gid, view="public") if e["type"] == "vote_tally")
    assert tally["target"] == 2 and tally["resolution"] == "exile"
    assert tally["counts"] == {str(p): int(p == 2) for p in range(1, 11)}


def test_human_malformed_vote_is_rejected_without_consuming_turn(tmp_path):
    app = create_app(tmp_path)
    _, created = request(app, "POST", "/games", payload())
    gid, token = created["game_id"], created["human_token"]
    engine = app.state.engine
    state = engine.games[gid]
    state.phase = "day_vote"
    state.day_voted = set(range(1, 10))
    pending = engine.advance_one_event(gid)
    path = f"/games/{gid}/players/10/action"
    body = {"action_id": pending["pending_action"]["action_id"], "expected_cursor": pending["cursor"]}
    for invalid in ({"action_id": []}, {"expected_cursor": False}, {"operation_id": {"id": "invalid"}}):
        request_body = {**body, "operation_id": "probe", "action": {"action": "vote", "target": 2}, **invalid}
        assert request(app, "POST", path, request_body, token)[0] == 422
        assert 10 not in state.day_voted and state.event_id == pending["cursor"]
    for index, (action, error) in enumerate([
        ({"action": [], "target": None}, "action must be a string"),
        ({"action": {}, "target": None}, "action must be a string"),
        ({"action": "vote"}, "vote target is required"),
    ]):
        code, result = request(app, "POST", path, dict(body, operation_id=f"invalid-{index}", action=action), token)
        assert code == 200 and result["status"] == "rejected" and result["error"] == error
        assert 10 not in state.day_voted and state.event_id == pending["cursor"]
    code, result = request(app, "POST", path,
                           dict(body, operation_id="abstain", action={"action": "vote", "target": None}), token)
    assert code == 200 and result["status"] == "running"
    assert 10 in state.day_voted and state.votes[10] is None


def test_damaged_saved_games_do_not_block_healthy_games(tmp_path, caplog):
    engine = GameEngine(tmp_path, agent=StubAgent())
    conflict, malformed, healthy = [engine.create_game(seed)["game_id"] for seed in range(3)]
    engine._event(engine.games[conflict], "game_paused", "host", timestamp="2000-01-01T00:00:00Z")
    engine = GameEngine(tmp_path, agent=StubAgent())
    with pytest.raises(ValueError, match="Conflicting event commit"):
        engine.pause_game(conflict)
    broken_state = tmp_path / malformed / "state.json"
    broken_state.write_text("{", encoding="utf-8")
    paths = [broken_state, *(tmp_path / conflict).iterdir()]
    before = {path: path.read_bytes() for path in paths}

    app = create_app(tmp_path)
    assert app.state.engine.unavailable_games == {conflict, malformed}
    assert request(app, "GET", f"/games/{conflict}/status")[0] == 409
    assert request(app, "GET", f"/games/{malformed}/replay")[0] == 409
    assert request(app, "GET", f"/games/{healthy}/status")[1]["status"] == "running"
    assert request(app, "POST", "/games", payload(False))[0] == 200
    assert all(gid in caplog.text for gid in (conflict, malformed))
    assert {path: path.read_bytes() for path in paths} == before


@pytest.mark.parametrize("remaining", [b'{"event_id":', b""])
def test_truncated_event_log_without_journal_is_isolated(tmp_path, remaining):
    engine = GameEngine(tmp_path, agent=StubAgent())
    damaged = engine.create_game(42)["game_id"]
    healthy = engine.create_game()["game_id"]
    log = tmp_path / damaged / "events.jsonl"
    log.write_bytes(remaining)
    assert not (log.parent / "vote_commit.json").exists()

    app = create_app(tmp_path)
    assert app.state.engine.unavailable_games == {damaged}
    for endpoint in ("status", "events", "replay"):
        assert request(app, "GET", f"/games/{damaged}/{endpoint}")[0] == 409
    assert request(app, "GET", f"/games/{healthy}/status")[1]["status"] == "running"
    assert log.read_bytes() == remaining


@pytest.mark.parametrize("event_ids, cursor, overrides", [
    ([3], 3, {}), ([2, 4], 4, {}), ([2], 3, {}), ([], 2, {}),
    ([2], 2, {"seed": "invalid"}), ([2], 2, {"roles": []}),
])
def test_invalid_event_commit_is_isolated_before_modifying_files(tmp_path, event_ids, cursor, overrides):
    engine = GameEngine(tmp_path, agent=StubAgent())
    damaged = engine.create_game(42)["game_id"]
    healthy = engine.create_game(43)["game_id"]
    folder = tmp_path / damaged
    state = dict(engine.games[damaged].to_dict(), status="paused", event_id=cursor, **overrides)
    initial, = engine.store.read(damaged)
    events = [dict(initial, type="game_paused", event_id=event_id) for event_id in event_ids]
    journal = folder / "vote_commit.json"
    journal.write_text(json.dumps({"state": state, "events": events, "summary": {}}), encoding="utf-8")
    before = {file.name: file.read_bytes() for file in folder.iterdir()}

    app = create_app(tmp_path)
    assert app.state.engine.unavailable_games == {damaged}
    assert request(app, "GET", f"/games/{damaged}/status")[0] == 409
    assert request(app, "GET", f"/games/{healthy}/status")[1]["status"] == "running"
    assert {file.name: file.read_bytes() for file in folder.iterdir()} == before


def test_browser_cannot_supply_provider_secrets_or_multiple_humans(tmp_path):
    app = create_app(tmp_path)
    value = payload()
    value["profiles"] = []
    assert request(app, "POST", "/games", value)[0] == 422
    value = payload()
    value["seats"][0].update(control_type="human", model_profile_id=None)
    assert request(app, "POST", "/games", value)[0] == 422
