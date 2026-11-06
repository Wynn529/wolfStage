import asyncio
from dataclasses import asdict
import json
from pathlib import Path
from urllib.parse import urlsplit

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
    assert len([e for e in engine.store.read(gid) if e["type"] == "vote"]) == 1
    assert engine.store.read_calls(gid) == []
    body["action"]["reason"] = "fake"
    assert request(app, "POST", path, body, token)[0] == 422


def test_browser_cannot_supply_provider_secrets_or_multiple_humans(tmp_path):
    app = create_app(tmp_path)
    value = payload()
    value["profiles"] = []
    assert request(app, "POST", "/games", value)[0] == 422
    value = payload()
    value["seats"][0].update(control_type="human", model_profile_id=None)
    assert request(app, "POST", "/games", value)[0] == 422
