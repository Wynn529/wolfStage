from __future__ import annotations

import uuid
import os
import threading
import random
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .agents import DeepSeekAgent, StubAgent, build_context
from .events import EventStore
from .deepseek_client import ModelOutputError
from .metrics import summarize
from .rules import GameState, RULE_VERSION, assign_roles, check_action, tally_votes, winner


class GameEngine:
    """协调规则状态机、代理调用、A 分类、事件日志和状态恢复。"""
    def __init__(self, runs_dir: str = "runs", agent: Any | None = None, classifier: Any | None = None):
        """初始化证据目录、代理和分类器，并恢复已有状态快照。"""
        self.store = EventStore(runs_dir)
        self.games: dict[str, GameState] = {}
        self.agent = agent or (DeepSeekAgent() if os.getenv("DEEPSEEK_API_KEY") else StubAgent(classifier))
        self.classifier = classifier
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        for game_id in self.store.list_games():
            saved = self.store.read_state(game_id)
            if saved:
                self.games[game_id] = GameState.from_dict(saved)

    def _event(self, state: GameState, typ: str, visibility: Any = "public", **kwargs: Any) -> dict[str, Any]:
        """生成连续 event_id、补充统一元数据并追加事件日志。"""
        state.event_id += 1
        source = kwargs.pop("source", "rules")
        event = {"game_id": state.game_id, "event_id": state.event_id, "timestamp": datetime.now(timezone.utc).isoformat(), "phase": state.phase, "day": state.day, "type": typ, "visibility": visibility, "rules_version": RULE_VERSION, "source": source, **kwargs}
        self.store.append(state.game_id, event)
        return event

    def create_game(self, seed: int | None = None, role_config: dict[int, str] | None = None) -> dict[str, Any]:
        """创建固定六人对局、写入初始事件和可恢复状态。"""
        game_id = uuid.uuid4().hex[:12]
        state = GameState(game_id, seed, assign_roles(seed, role_config))
        self.games[game_id] = state
        self._event(state, "game_created", "host", roles=state.roles, alive=sorted(state.alive))
        self.store.write_state(game_id, state.to_dict())
        return {"game_id": game_id, "status": state.status, "phase": state.phase, "rules_version": RULE_VERSION}

    def pause_game(self, game_id: str) -> dict[str, Any]:
        """在当前事件边界暂停运行中的对局并持久化状态。"""
        state = self.games[game_id]
        if state.status == "waiting_model":
            state.pause_requested = True
            self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": "waiting_model", "pause_requested": True}
        with self._locks[game_id]:
            if state.status == "running":
                state.status = "paused"
                self._event(state, "game_paused", "host")
                self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": state.status}

    def resume_game(self, game_id: str) -> dict[str, Any]:
        """恢复已暂停对局，后续 advance 可继续推进。"""
        with self._locks[game_id]:
            state = self.games[game_id]
            state.pause_requested = False
            if state.status == "paused":
                state.status = "running"
                self._event(state, "game_resumed", "host")
                self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": state.status}

    def _call_agent(self, state: GameState, actor: int) -> dict[str, Any]:
        """构造角色可见上下文、调用代理、校验行动并记录成功/失败/重试。"""
        events = self.store.read(state.game_id)
        role = state.roles[actor]
        context = build_context(state, events, actor, role)
        visible_ids = [event["event_id"] for event in context["events"]]
        call_id = f"{state.game_id}:player:{state.event_id + 1}:{actor}:{uuid.uuid4().hex[:8]}"
        base_call = {"call_id":call_id, "game_id":state.game_id, "event_id":state.event_id+1,
                     "phase":state.phase, "caller_type":"player", "agent_id":actor,
                     "model_name":getattr(self.agent,"model_name","unknown"),
                     "model_version":getattr(self.agent,"model_version","unknown"),
                     "prompt_version":f"{role}-v1", "input_visibility":actor,
                     "input_event_ids":visible_ids, "fallback":False}
        for attempt in (1, 2):
            state.status = "waiting_model"
            metadata = {}
            action = None
            retryable = False
            failure_stage = None
            try:
                action = self.agent.act(state, actor, context)
                metadata = dict(getattr(self.agent, "last_metadata", {}) or {})
                error = check_action(state, actor, action)
                retryable = error is not None
                failure_stage = "action_validation" if error else None
            except Exception as exc:
                metadata = dict(getattr(self.agent,"last_metadata",{}) or {})
                metadata.update(getattr(exc,"metadata",{}) or {})
                error = str(exc)
                retryable = isinstance(exc, ModelOutputError)
                failure_stage = "output_parsing" if retryable else "request"
            call = dict(base_call, attempt=attempt, success=error is None,
                        validation_error=error, failure_stage=failure_stage,
                        latency_ms=metadata.get("latency_ms"), input_tokens=metadata.get("input_tokens"),
                        output_tokens=metadata.get("output_tokens"))
            for key in ("prompt_hash", "response_id", "returned_model"):
                if key in metadata:
                    call[key] = metadata[key]
            self.store.append_call(state.game_id, call)
            if error is None:
                state.status = "waiting_model" if state.pause_requested else "running"
                return action
            self.store.append_error(state.game_id, {"event_id":state.event_id+1, "actor":actor,
                                                   "attempt":attempt, "error":error, "failure_stage":failure_stage})
            if attempt == 1 and retryable:
                context = dict(context, validation_error=error)
                continue
            state.status = "failed"
            self._event(state,"model_failure",[actor],actor=actor,error=error)
            return {"action":"invalid", "target":None, "text":"", "reason":error}

    def _check_end(self, state: GameState, *, check_limit: bool = True) -> bool:
        """在夜间死亡或白天放逐后检查胜负和八昼夜上限。"""
        win = winner(state)
        if win:
            state.winner, state.status = win, "finished"
            self._event(state, "game_over", "public", winner=win, alive=sorted(state.alive))
            return True
        if check_limit and state.day >= 8:
            state.status = "incomplete"
            self._event(state, "game_limit", "public", winner=None)
            return True
        return False

    def advance_one_event(self, game_id: str, operation_id: str | None = None, expected_cursor: int | None = None) -> dict[str, Any]:
        """在同局互斥锁内幂等推进一个操作；重复 operation_id 直接返回原结果。"""
        with self._locks[game_id]:
            state = self.games[game_id]
            if operation_id and operation_id in state.operation_results:
                return state.operation_results[operation_id]
            if expected_cursor is not None and expected_cursor != state.event_id:
                return {"game_id": game_id, "operation_id": operation_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            before_event_id = state.event_id
            state.operation_start_event_id = before_event_id
            response = self._advance_one_event_locked(game_id, operation_id)
            if operation_id:
                state.operation_results[operation_id] = response
            self.store.write_state(game_id, state.to_dict())
            return response

    def _advance_one_event_locked(self, game_id: str, operation_id: str | None = None) -> dict[str, Any]:
        """执行已取得同局锁的单步推进内部逻辑。"""
        state = self.games[game_id]
        if state.status != "running":
            return {"game_id": game_id, "status": state.status, "phase": state.phase, "new_event": None}
        if state.phase == "night":
            wolves = [p for p in sorted(state.alive) if state.roles[p] == "wolf" and p not in state.night_done]
            seers = [p for p in sorted(state.alive) if state.roles[p] == "seer" and "seer" not in state.night_done]
            if wolves:
                actor = wolves[0]; action = self._call_agent(state, actor)
                if state.status == "failed": return self._response(state, operation_id)
                if action.get("action") == "kill":
                    target = action["target"]; state.pending.setdefault("kill_proposals", {})[actor] = target
                    proposal_event = self._event(state, "wolf_proposal", [p for p in state.alive if state.roles[p] == "wolf"], actor=actor, target=target, source=getattr(self.agent, "model_name", "stub"))
                    self._event(state, "agent_reason", [actor, "host", "god"], actor=actor, source_event_id=proposal_event["event_id"], reason=action.get("reason"), source=getattr(self.agent, "model_name", "stub"))
                state.night_done.add(actor)
                return self._response(state, operation_id)
            if seers:
                actor = seers[0]; action = self._call_agent(state, actor)
                if state.status == "failed": return self._response(state, operation_id)
                if action.get("action") == "inspect":
                    target = action["target"]
                    self._event(state, "inspection", [actor, "host", "god"], actor=actor, target=target, result=state.roles[target], source=getattr(self.agent, "model_name", "stub"))
                state.night_done.add("seer")
                return self._response(state, operation_id)
            proposals = state.pending.get("kill_proposals", {})
            targets = list(proposals.values())
            target = targets[0] if targets and len(set(targets)) == 1 else (targets[0] if targets else None)
            if len(set(targets)) > 1:
                # 分歧只在狼人私密上下文中展示；规则程序固定选择排序后的首个有效提案。
                target = random.Random((state.seed or 0) + state.day).choice(sorted(set(targets)))
                self._event(state, "wolf_negotiation", [p for p in state.alive if state.roles[p] == "wolf"], proposals=proposals, resolved_target=target, resolution="seeded_rule")
            if target in state.alive:
                state.alive.remove(target)
                self._event(state, "night_death", "public", target=target)
            state.night_done.clear(); state.pending.clear(); state.day += 1; state.phase = "day_speak"; state.day_speakers.clear(); state.day_voted.clear(); state.votes.clear(); state.revote = False
            if self._check_end(state, check_limit=False): return self._response(state, operation_id)
            return self._response(state, operation_id)
        if state.phase == "day_speak":
            remaining = [p for p in sorted(state.alive) if p not in state.day_speakers]
            if remaining:
                actor = remaining[0]; action = self._call_agent(state, actor)
                if state.status == "failed": return self._response(state, operation_id)
                state.day_speakers.append(actor)
                event = self._event(state, "public_speech", "public", actor=actor, text=action.get("text", ""), source=getattr(self.agent, "model_name", "stub"))
                self._event(state, "agent_reason", [actor, "host", "god"], actor=actor, source_event_id=event["event_id"], reason=action.get("reason"), source=getattr(self.agent, "model_name", "stub"))
                if self.classifier:
                    try:
                        state.status = "waiting_model"
                        result = self.classifier(state.game_id, event["event_id"], actor, event["text"])
                        state.status = "waiting_model" if state.pause_requested else "running"
                        state.classification_by_event[event["event_id"]] = result
                        self.store.append_call(state.game_id, {"call_id": f"{state.game_id}:small:{event['event_id']}", "game_id": state.game_id, "event_id": event["event_id"], "phase": state.phase, "caller_type": "small_model", "agent_id": actor, "model_name": getattr(self.classifier, "model_name", result.get("model_version", "small-model")), "model_version": result.get("model_version"), "prompt_version": None, "input_visibility": "public", "input_event_ids": [event["event_id"]], "attempt": 1, "latency_ms": result.get("latency_ms"), "input_tokens": None, "output_tokens": None, "success": True, "fallback": False, "validation_error": None})
                        self._event(state, "utterance_classification", "public", source_event_id=event["event_id"], result=result, source=getattr(self.classifier, "model_name", "a-classifier"))
                    except Exception as exc:
                        state.status = "waiting_model" if state.pause_requested else "running"
                        self.store.append_call(state.game_id, {"call_id": f"{state.game_id}:small:{event['event_id']}", "game_id": state.game_id, "event_id": event["event_id"], "phase": state.phase, "caller_type": "small_model", "agent_id": actor, "model_name": getattr(self.classifier, "model_name", "small-model"), "model_version": None, "prompt_version": None, "input_visibility": "public", "input_event_ids": [event["event_id"]], "attempt": 1, "latency_ms": None, "input_tokens": None, "output_tokens": None, "success": False, "fallback": True, "validation_error": str(exc)})
                        state.status = "failed"
                        self.store.append_error(state.game_id, {"event_id": event["event_id"], "caller_type": "a_classifier", "error": str(exc)})
                        self._event(state, "utterance_classification_failed", "host", source_event_id=event["event_id"], error=str(exc), fallback=True)
                return self._response(state, operation_id)
            state.phase = "day_vote"; return self._response(state, operation_id)
        if state.phase == "day_vote":
            remaining = [p for p in sorted(state.alive) if p not in state.day_voted]
            if remaining:
                actor = remaining[0]; action = self._call_agent(state, actor)
                if state.status == "failed": return self._response(state, operation_id)
                state.day_voted.add(actor)
                target = action.get("target")
                if target in state.alive and target != actor: state.votes[actor] = target; valid = True
                else: valid = False
                vote_event = self._event(state, "vote", "public", actor=actor, target=target, valid=valid, source=getattr(self.agent, "model_name", "stub"))
                self._event(state, "agent_reason", [actor, "host", "god"], actor=actor, source_event_id=vote_event["event_id"], reason=action.get("reason"), source=getattr(self.agent, "model_name", "stub"))
                return self._response(state, operation_id)
            target, counts = tally_votes(state.votes)
            self._event(state, "vote_tally", "public", counts=counts, target=target, revote=state.revote)
            if target is None and counts and not state.revote:
                leaders = [candidate for candidate, count in counts.items() if count == max(counts.values())] if counts else []
                state.pending["revote_candidates"] = sorted(leaders)
                state.revote = True; state.day_voted.clear(); state.votes.clear(); return self._response(state, operation_id)
            if target in state.alive: state.alive.remove(target); self._event(state, "exile", "public", target=target)
            if self._check_end(state): return self._response(state, operation_id)
            state.phase = "night"; state.night_done.clear(); state.pending.clear(); state.revote = False
            return self._response(state, operation_id)
        return self._response(state, operation_id)

    def _response(self, state: GameState, operation_id: str | None = None) -> dict[str, Any]:
        """保存最新状态并生成供 API/C 使用的统一推进响应。"""
        before = state.operation_start_event_id if state.operation_start_event_id is not None else state.event_id
        state.operation_start_event_id = None
        new_events = list(range(before + 1, state.event_id + 1))
        if state.pause_requested and state.status in {"running", "waiting_model"}:
            state.status = "paused"
            state.pause_requested = False
            self._event(state, "game_paused", "host")
            new_events = list(range(before + 1, state.event_id + 1))
        summary = summarize(self.store, state.game_id)
        self.store.write_state(state.game_id, state.to_dict())
        response = {"game_id": state.game_id, "operation_id": operation_id, "new_event": state.event_id if new_events else None, "new_events": new_events, "cursor": state.event_id, "phase": state.phase, "status": state.status, "summary": summary}
        return response

    def get_status(self, game_id: str) -> dict[str, Any]:
        """返回当前阶段、状态、游标和最后已持久化事件，不推进游戏。"""
        state = self.games[game_id]
        return {"game_id": game_id, "status": state.status, "pause_requested": state.pause_requested, "phase": state.phase, "day": state.day, "cursor": state.event_id, "winner": state.winner}

    def get_operation(self, game_id: str, operation_id: str) -> dict[str, Any]:
        """只读查询指定操作是否已完成以及其原始响应。"""
        state = self.games[game_id]
        if operation_id in state.operation_results:
            return {"game_id": game_id, "operation_id": operation_id, "status": "completed", "result": state.operation_results[operation_id]}
        return {"game_id": game_id, "operation_id": operation_id, "status": "unknown", "result": None}

    def get_events(self, game_id: str, after_event_id: int = 0, view: str = "god") -> list[dict[str, Any]]:
        """按事件游标和玩家/主持/上帝视角返回可见事件。"""
        events = [e for e in self.store.read(game_id) if e["event_id"] > after_event_id]
        if view == "god": return events
        if view.isdigit():
            viewer = int(view)
            return [e for e in events if e.get("visibility") == "public" or viewer in (e.get("visibility") if isinstance(e.get("visibility"), list) else [])]
        return [e for e in events if e.get("visibility") == "public"]

    def get_metrics(self, game_id: str) -> dict[str, Any]:
        """读取并重算指定对局的调用与规则统计。"""
        return summarize(self.store, game_id)

    def replay_game(self, game_id: str) -> list[dict[str, Any]]:
        """只读回放原始事件，不重新调用代理或外部模型。"""
        return self.store.replay(game_id)
