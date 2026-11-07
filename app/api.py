from __future__ import annotations

from typing import Any
import os

try:
    from fastapi import FastAPI, HTTPException
except ImportError:  # pragma: no cover
    FastAPI = None

from .engine import GameEngine
from .classifier import AHttpClassifier

classifier = AHttpClassifier() if os.getenv("A_CLASSIFIER_URL") else None
engine = GameEngine(classifier=classifier)
app = FastAPI(title="Werewolf Game Service", version="1.0.0") if FastAPI else None


if app:
    @app.post("/games")
    def create_game(payload: dict[str, Any] | None = None):
        """创建十人游戏；可按席位指定真人控制器和模型配置。"""
        payload = payload or {}
        return engine.create_game(payload.get("seed"), payload.get("role_config"),
                                  seat_controllers=payload.get("seat_controllers"),
                                  seat_model_configs=payload.get("seat_model_configs"),
                                  player_count=payload.get("player_count", 10))

    @app.post("/games/{game_id}/advance")
    def advance(game_id: str, payload: dict[str, Any] | None = None):
        """只推进一个业务操作；重复 operation_id 返回同一结果。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        payload = payload or {}
        return engine.advance_one_event(game_id, payload.get("operation_id"), payload.get("expected_cursor"))

    @app.post("/games/{game_id}/pause")
    def pause(game_id: str):
        """在事件边界暂停对局。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.pause_game(game_id)

    @app.post("/games/{game_id}/resume")
    def resume(game_id: str):
        """恢复已暂停对局。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.resume_game(game_id)

    @app.get("/games/{game_id}/events")
    def events(game_id: str, after_event_id: int = 0, view: str = "god", player_id: int | None = None):
        """按游标读取指定视角可见的事件。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_events(game_id, after_event_id, view, player_id)

    @app.get("/games/{game_id}/pending-action")
    def pending_action(game_id: str):
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_status(game_id).get("pending_action")

    @app.get("/games/{game_id}/players/{player_id}/identity")
    def player_identity(game_id: str, player_id: int):
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        try:
            return engine.get_player_identity(game_id, player_id)
        except ValueError as exc:
            raise HTTPException(403, str(exc))

    @app.post("/games/{game_id}/players/{player_id}/action")
    def human_action(game_id: str, player_id: int, payload: dict[str, Any] | None = None):
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        payload = payload or {}
        action = payload.get("action")
        if not isinstance(action, dict): raise HTTPException(422, "action must be an object")
        return engine.submit_human_action(game_id, player_id, action,
                                          action_id=payload.get("action_id"),
                                          expected_cursor=payload.get("expected_cursor"),
                                          operation_id=payload.get("operation_id"))

    @app.post("/games/{game_id}/players/{player_id}/wolf-discussion")
    def wolf_discussion(game_id: str, player_id: int, payload: dict[str, Any] | None = None):
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        payload = payload or {}
        return engine.submit_wolf_discussion(game_id, player_id, payload.get("text", ""),
                                             expected_cursor=payload.get("expected_cursor"))

    @app.get("/games/{game_id}/status")
    def status(game_id: str):
        """只读查询等待、暂停、游标和胜负状态。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_status(game_id)

    @app.get("/games/{game_id}/operations/{operation_id}")
    def operation(game_id: str, operation_id: str):
        """只读查询一次推进操作的结果。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_operation(game_id, operation_id)

    @app.get("/games/{game_id}/metrics")
    def metrics(game_id: str):
        """返回从原始调用日志重算的场次统计。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_metrics(game_id)

    @app.get("/games/{game_id}/replay")
    def replay(game_id: str):
        """读取只读回放事件，不重新调用任何模型。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.replay_game(game_id)
