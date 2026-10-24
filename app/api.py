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
        """创建一局固定六人游戏并返回 game_id。"""
        payload = payload or {}
        return engine.create_game(payload.get("seed"), payload.get("role_config"))

    @app.post("/games/{game_id}/advance")
    def advance(game_id: str):
        """只推进指定对局的一个事件。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.advance_one_event(game_id)

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
    def events(game_id: str, after_event_id: int = 0, view: str = "god"):
        """按游标读取指定视角可见的事件。"""
        if game_id not in engine.games: raise HTTPException(404, "unknown game")
        return engine.get_events(game_id, after_event_id, view)

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
