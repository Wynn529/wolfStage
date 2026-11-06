"""十人/九模型联调入口。旧 app.api 与 c_live_service 保留供历史回归。"""
from dataclasses import asdict
import json
import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from .agents import StubAgent, action_contract, build_context
from .engine import GameEngine
from .human_service import HumanService
from .seat_config import MatchConfig

ROOT = Path(__file__).resolve().parents[1]


def create_app(runs_dir=None, profile_path=None, classifier=None):
    directory = Path(runs_dir or ROOT / ".local/integration_runs")
    directory.mkdir(parents=True, exist_ok=True)
    registry = MatchConfig.from_dict(json.loads(Path(profile_path or ROOT / "config/nine_models.example.json").read_text(encoding="utf-8")))
    if classifier is None:
        from model.predict_v04 import SmallModel
        model = SmallModel()
        def classifier(gid, eid, seat, text):
            return model.classify_utterance(gid, str(eid), seat, text)
        classifier.model_name = model.manifest["model_version"]
    engine = GameEngine(str(directory), agent=StubAgent(), classifier=classifier)
    sessions = HumanService(directory / "human_sessions.sqlite")
    app = FastAPI(title="Ten player integration", version="2.0")
    app.state.engine = engine
    app.state.sessions = sessions

    def state(gid):
        if gid not in engine.games:
            raise HTTPException(404, "unknown game")
        return engine.games[gid]

    def authorize(gid, authorization, *, mutate=False):
        s = state(gid)
        humans = [p for p, c in s.seat_controllers.items() if c == "human"]
        if not humans or (s.status == "finished" and not mutate):
            return None
        token = (authorization or "").removeprefix("Bearer ")
        try:
            return sessions.authenticate(gid, token)
        except PermissionError:
            raise HTTPException(403, "human session required") from None

    @app.get("/model-profiles")
    def profiles():
        return [{"profile_id": p.profile_id, "display_name": p.display_name,
                 "provider": p.provider, "model": p.model} for p in registry.profiles]

    @app.post("/games")
    def create(payload: dict):
        if set(payload) - {"mode", "seats", "human_role", "seed"}:
            raise HTTPException(422, "use registered model_profile_id only")
        try:
            config = MatchConfig.from_dict(dict(payload, profiles=[asdict(p) for p in registry.profiles]))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(422, "invalid ten-seat configuration") from None
        # 建局不发请求；需要时才读取对应模型环境变量。
        result = engine.create_game(payload.get("seed"), match_config=config)
        human = next((s.seat_id for s in config.seats if s.control_type == "human"), None)
        if human is not None:
            result["human_token"] = sessions.bind(result["game_id"], human)
            result["human_seat"] = human
        return result

    @app.get("/games/{gid}/status")
    def status(gid: str, authorization: str | None = Header(default=None)):
        seat = authorize(gid, authorization)
        result = engine.get_status(gid)
        if seat is not None:
            s = state(gid)
            result["player_context"] = build_context(s, engine.store.read(gid), seat, s.roles[seat])
            if s.pending_action and s.pending_action["actor"] == seat:
                result["action_contract"] = action_contract(s, seat)
        return result

    @app.get("/games/{gid}/events")
    def events(gid: str, after_event_id: int = 0, view: str = "god", player_id: int | None = None,
               authorization: str | None = Header(default=None)):
        seat = authorize(gid, authorization)
        if seat is not None:
            if player_id is not None and player_id != seat:
                raise HTTPException(403, "seat override forbidden")
            return engine.get_events(gid, after_event_id, str(seat), seat)
        return engine.store.read(gid)[after_event_id:]

    @app.post("/games/{gid}/advance")
    def advance(gid: str, payload: dict, authorization: str | None = Header(default=None)):
        authorize(gid, authorization, mutate=True)
        return engine.advance_one_event(gid, payload.get("operation_id"), payload.get("expected_cursor"))

    @app.post("/games/{gid}/pause")
    def pause(gid: str, authorization: str | None = Header(default=None)):
        authorize(gid, authorization, mutate=True)
        return engine.pause_game(gid)

    @app.post("/games/{gid}/resume")
    def resume(gid: str, authorization: str | None = Header(default=None)):
        authorize(gid, authorization, mutate=True)
        return engine.resume_game(gid)

    @app.post("/games/{gid}/players/{player_id}/action")
    def submit(gid: str, player_id: int, payload: dict, authorization: str | None = Header(default=None)):
        seat = authorize(gid, authorization, mutate=True)
        if seat != player_id:
            raise HTTPException(403, "seat override forbidden")
        if set(payload) - {"action", "action_id", "expected_cursor", "operation_id"}:
            raise HTTPException(422, "unexpected fields")
        action = payload.get("action")
        if not isinstance(action, dict) or set(action) - {"action", "target", "text"}:
            raise HTTPException(422, "human submits action/target/text only")
        if not all(payload.get(k) is not None for k in ("action_id", "expected_cursor", "operation_id")):
            raise HTTPException(422, "action_id/cursor/operation_id required")
        return engine.submit_human_action(gid, seat, action, action_id=payload["action_id"],
                                           expected_cursor=payload["expected_cursor"], operation_id=payload["operation_id"])

    @app.get("/games/{gid}/metrics")
    def metrics(gid: str, authorization: str | None = Header(default=None)):
        authorize(gid, authorization)
        return engine.get_metrics(gid)

    @app.get("/games/{gid}/operations/{operation_id}")
    def operation(gid: str, operation_id: str, authorization: str | None = Header(default=None)):
        authorize(gid, authorization)
        return engine.get_operation(gid, operation_id)

    @app.get("/games/{gid}/replay")
    def replay(gid: str):
        s = state(gid)
        if any(c == "human" for c in s.seat_controllers.values()) and s.status != "finished":
            raise HTTPException(403, "human replay requires finished game")
        return engine.replay_game(gid)

    return app
