from __future__ import annotations

import os
import random
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .agents import DeepSeekAgent, SeatAgentRouter, StubAgent, build_context, legal_actions_for
from .deepseek_client import ModelOutputError
from .events import EventStore
from .metrics import summarize
from .rules import GameState, RULE_VERSION, assign_roles, check_action, tally_votes, winner


class GameEngine:
    """十人规则状态机，统一驱动 AI 席位和真人席位。"""

    def __init__(self, runs_dir: str = "runs", agent: Any | None = None, classifier: Any | None = None, agents: dict[str, Any] | None = None):
        self.store = EventStore(runs_dir)
        self.games: dict[str, GameState] = {}
        base = agent or (DeepSeekAgent() if os.getenv("DEEPSEEK_API_KEY") else StubAgent(classifier))
        self.agent = SeatAgentRouter(base, agents) if agents else base
        self.classifier = classifier
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._agent_lock = threading.RLock()
        self.max_player_calls = int(os.getenv("B_MAX_PLAYER_CALLS", "200"))
        self.max_small_model_calls = int(os.getenv("B_MAX_SMALL_MODEL_CALLS", "200"))
        for game_id in self.store.list_games():
            saved = self.store.read_state(game_id)
            if saved:
                self.games[game_id] = GameState.from_dict(saved)

    def _event(self, state: GameState, typ: str, visibility: Any = "public", **kwargs: Any) -> dict[str, Any]:
        state.event_id += 1
        source = kwargs.pop("source", "rules")
        event = {"game_id": state.game_id, "event_id": state.event_id, "timestamp": datetime.now(timezone.utc).isoformat(), "phase": state.phase, "day": state.day, "type": typ, "visibility": visibility, "rules_version": state.rules_version, "source": source, **kwargs}
        self.store.append(state.game_id, event)
        return event

    def create_game(self, seed: int | None = None, role_config: dict[int, str] | None = None, *, seat_controllers: dict[int, str] | None = None, seat_model_configs: dict[int, dict[str, Any]] | None = None, player_count: int = 10) -> dict[str, Any]:
        game_id = uuid.uuid4().hex[:12]
        roles = assign_roles(seed, role_config, player_count=player_count)
        controllers = {p: "ai" for p in roles}
        if seat_controllers:
            controllers.update({int(k): v for k, v in seat_controllers.items()})
        if sum(v == "human" for v in controllers.values()) > 1:
            raise ValueError("this local mode supports one human seat only")
        if any(v not in {"ai", "human"} for v in controllers.values()):
            raise ValueError("seat_controllers values must be ai or human")
        state = GameState(game_id, seed, roles, seat_controllers=controllers, seat_model_configs=seat_model_configs or {}, rules_version=RULE_VERSION if len(roles) == 10 else "rules-v1.0")
        self.games[game_id] = state
        self._event(state, "game_created", "host", roles=state.roles, alive=sorted(state.alive), seat_controllers=state.seat_controllers, model_configs=state.seat_model_configs)
        self.store.write_state(game_id, state.to_dict())
        return {"game_id": game_id, "status": state.status, "phase": state.phase, "rules_version": state.rules_version, "players": sorted(state.roles), "seat_controllers": state.seat_controllers}

    def pause_game(self, game_id: str) -> dict[str, Any]:
        state = self.games[game_id]
        with self._locks[game_id]:
            if state.status in {"waiting_model", "waiting_human"}:
                state.pause_requested = True; self.store.write_state(game_id, state.to_dict())
                return {"game_id": game_id, "status": state.status, "pause_requested": True}
            if state.status == "running":
                state.status = "paused"; self._event(state, "game_paused", "host"); self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": state.status}

    def resume_game(self, game_id: str) -> dict[str, Any]:
        with self._locks[game_id]:
            state = self.games[game_id]; state.pause_requested = False
            if state.status == "paused": state.status = "running"; self._event(state, "game_resumed", "host")
            self.store.write_state(game_id, state.to_dict()); return {"game_id": game_id, "status": state.status}

    def _call_agent(self, state: GameState, actor: int) -> dict[str, Any]:
        events = self.store.read(state.game_id); role = state.roles[actor]; context = build_context(state, events, actor, role)
        config = state.seat_model_configs.get(actor, {})
        base = {"game_id": state.game_id, "event_id": state.event_id + 1, "phase": state.phase, "caller_type": "player", "agent_id": actor, "seat_id": actor, "model_name": config.get("model_name") or config.get("model") or getattr(self.agent, "model_name", "unknown"), "model_version": config.get("model_version") or getattr(self.agent, "model_version", "unknown"), "prompt_version": config.get("prompt_version", f"{role}-v1"), "input_visibility": actor, "input_event_ids": [e["event_id"] for e in context["events"]], "fallback": False}
        used = sum(1 for c in self.store.read_calls(state.game_id) if c.get("caller_type") == "player")
        if used >= self.max_player_calls:
            state.status = "failed"; self._event(state, "budget_exceeded", [actor, "host", "god"], actor=actor, caller_type="player", limit=self.max_player_calls)
            return {"action": "pass", "target": None, "text": "", "reason": "player call limit exceeded"}
        for attempt in (1, 2):
            state.status = "waiting_model"; metadata: dict[str, Any] = {}
            try:
                with self._agent_lock:
                    action = self.agent.act(state, actor, context)
                    metadata = dict(getattr(self.agent, "last_metadata", {}) or {})
                error = check_action(state, actor, action); retryable = error is not None; stage = "action_validation" if error else None
            except Exception as exc:
                action = {"action": "pass", "target": None, "text": "", "reason": str(exc)}; metadata = dict(getattr(self.agent, "last_metadata", {}) or {}); metadata.update(getattr(exc, "metadata", {}) or {}); error = str(exc); retryable = isinstance(exc, ModelOutputError); stage = "output_parsing" if retryable else "request"
                retryable = retryable or any(token in error.lower() for token in
                                             ("500", "502", "503", "504", "timed out", "ssl", "unexpected_eof"))
            call = dict(base, attempt=attempt, success=error is None, validation_error=error, failure_stage=stage, latency_ms=metadata.get("latency_ms"), input_tokens=metadata.get("input_tokens"), output_tokens=metadata.get("output_tokens"), model_name=metadata.get("model_name", base["model_name"]), model_version=metadata.get("model_version", base["model_version"]))
            for key in ("prompt_hash", "response_id", "returned_model", "request_evidence", "request_sha256"):
                if key in metadata: call[key] = metadata[key]
            self.store.append_call(state.game_id, call)
            if error is None:
                state.status = "waiting_model" if state.pause_requested else "running"; return action
            self.store.append_error(state.game_id, {"event_id": state.event_id + 1, "actor": actor, "attempt": attempt, "error": error, "failure_stage": stage})
            if attempt == 1 and retryable:
                context = dict(context, validation_error=error); continue
            fallback = self._fallback_action(state, actor, error)
            state.status = "running"
            self._event(state, "model_fallback", [actor, "host", "god"], actor=actor,
                        error=error, action=fallback["action"], target=fallback.get("target"))
            return fallback
        return {"action": "pass", "target": None, "text": "", "reason": "unreachable"}

    def _fallback_action(self, state: GameState, actor: int, error: str) -> dict[str, Any]:
        """模型失败时生成当前阶段一定合法的最小行动。"""
        role = state.roles.get(actor)
        if state.phase == "day_speak":
            return {"action": "speak", "target": None,
                    "text": f"{actor}号暂按公开信息发言，等待更多玩家提供判断。", "reason": "model fallback"}
        if state.phase == "day_vote":
            return {"action": "pass", "target": None, "text": "", "reason": "model fallback"}
        if state.phase == "night" and role == "wolf":
            targets = [p for p in sorted(state.alive) if p != actor and state.roles.get(p) != "wolf"]
            if targets:
                target = targets[((state.seed or 0) + state.day + actor) % len(targets)]
                return {"action": "kill", "target": target, "text": "", "reason": "model fallback"}
        if state.phase == "night" and role == "seer":
            targets = [p for p in sorted(state.alive) if p != actor and p not in state.inspection_history]
            if targets:
                return {"action": "inspect", "target": targets[0], "text": "", "reason": "model fallback"}
        return {"action": "pass", "target": None, "text": "", "reason": "model fallback"}

    def _action_for(self, state: GameState, actor: int) -> dict[str, Any] | None:
        if state.seat_controllers.get(actor, "ai") == "human":
            state.pending_action = {"actor": actor, "phase": state.phase, "action_id": state.pending_action.get("action_id") if state.pending_action else f"{state.game_id}:{state.event_id + 1}:{actor}"}
            state.status = "waiting_human"; self._event(state, "waiting_human", [actor, "host", "god"], actor=actor, action_id=state.pending_action["action_id"]); return None
        return self._call_agent(state, actor)

    def submit_human_action(self, game_id: str, actor: int, action: dict[str, Any], *, action_id: str | None = None, expected_cursor: int | None = None, operation_id: str | None = None) -> dict[str, Any]:
        with self._locks[game_id]:
            state = self.games[game_id]
            if state.seat_controllers.get(actor) != "human": return {"game_id": game_id, "status": "rejected", "error": "seat_is_not_human"}
            if expected_cursor is not None and expected_cursor != state.event_id: return {"game_id": game_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            pending = state.pending_action or {}
            if pending.get("actor") != actor or (action_id and pending.get("action_id") != action_id): return {"game_id": game_id, "status": "rejected", "error": "pending_action_mismatch"}
            error = check_action(state, actor, action)
            if error: self.store.append_error(game_id, {"actor": actor, "error": error, "source": "human"}); return {"game_id": game_id, "status": "rejected", "error": error}
            state.pending["submitted_action"] = action; state.pending_action = None; state.status = "running"; self._event(state, "human_action_submitted", [actor, "host", "god"], actor=actor, action=action, source="human")
            if state.phase == "night" and state.roles.get(actor) == "wolf":
                discussion = action.get("text") or action.get("reason")
                if discussion:
                    wolves = [p for p in state.alive if state.roles.get(p) == "wolf"]
                    self._event(state, "wolf_discussion", wolves + ["host", "god"], actor=actor,
                                text=discussion, source="human")
            self.store.write_state(game_id, state.to_dict())
            return self._advance_one_event_locked(game_id, operation_id)

    def submit_wolf_discussion(self, game_id: str, actor: int, text: str, *, expected_cursor: int | None = None) -> dict[str, Any]:
        with self._locks[game_id]:
            state = self.games[game_id]
            if state.seat_controllers.get(actor) != "human" or state.roles.get(actor) != "wolf":
                return {"game_id": game_id, "status": "rejected", "error": "seat_is_not_human_wolf"}
            if state.status != "waiting_human" or not state.pending_action or state.pending_action.get("actor") != actor:
                return {"game_id": game_id, "status": "rejected", "error": "not_waiting_for_wolf"}
            if expected_cursor is not None and expected_cursor != state.event_id:
                return {"game_id": game_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            if not isinstance(text, str) or not text.strip() or len(text) > 500:
                return {"game_id": game_id, "status": "rejected", "error": "discussion_text_invalid"}
            wolves = [p for p in state.alive if state.roles.get(p) == "wolf"]
            self._event(state, "wolf_discussion", wolves + ["host", "god"], actor=actor,
                        text=text.strip(), source="human")
            self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": "accepted", "cursor": state.event_id}

    def _consume_action(self, state: GameState, actor: int) -> dict[str, Any] | None:
        action = state.pending.pop("submitted_action", None)
        return action if action is not None else self._action_for(state, actor)

    def _record_reason(self, state: GameState, actor: int, event: dict[str, Any], action: dict[str, Any], source: str) -> None:
        if action.get("reason"):
            self._event(state, "agent_reason", [actor, "host", "god"], actor=actor, source_event_id=event["event_id"], reason=action.get("reason"), source=source)

    def _check_end(self, state: GameState, check_limit: bool = True) -> bool:
        if state.phase in {"hunter_action", "last_words"}: return False
        result = winner(state)
        if result:
            state.winner, state.status = result, "finished"; self._event(state, "game_over", "public", winner=result, alive=sorted(state.alive)); return True
        if check_limit and state.day >= 8:
            state.status = "incomplete"; self._event(state, "game_limit", "public", winner=None); return True
        return False

    def _resolve_night(self, state: GameState) -> None:
        proposals = state.pending.get("kill_proposals", {}); targets = list(proposals.values()); kill_target = None
        if targets:
            counts = {p: targets.count(p) for p in set(targets)}; high = max(counts.values()); leaders = sorted(p for p, n in counts.items() if n == high); kill_target = random.Random((state.seed or 0) + state.day).choice(leaders)
            if len(leaders) > 1 or len(set(targets)) > 1: self._event(state, "wolf_negotiation", [p for p in state.alive if state.roles[p] == "wolf"], proposals=proposals, resolved_target=kill_target, resolution="majority_seeded_tie")
        state.pending["night_kill_target"] = kill_target
        deaths = {}
        if kill_target and not state.pending.get("saved"):
            deaths[kill_target] = "wolf_kill"
        poison = state.pending.get("poison_target")
        if poison in state.alive:
            # Poison takes precedence for hunter eligibility, including knife+poison.
            deaths[poison] = "witch_poison"
        for target, reason in deaths.items():
            if target in state.alive:
                state.alive.remove(target)
                state.death_reasons[target] = reason
                self._event(state, "night_death", "public", target=target)
        hunter = next((p for p, reason in deaths.items() if state.roles.get(p) == "hunter"
                       and reason == "wolf_kill" and not state.hunter_processed), None)
        if hunter is not None:
            self._start_hunter(state, hunter, "after_night")
            return
        self._start_day(state)

    def _classify_speech(self, state: GameState, event: dict[str, Any], actor: int) -> None:
        if self.classifier:
            try:
                result = self.classifier(state.game_id, event["event_id"], actor, event["text"]); state.classification_by_event[event["event_id"]] = result; self.store.append_call(state.game_id, {"call_id": f"{state.game_id}:small:{event['event_id']}", "game_id": state.game_id, "event_id": event["event_id"], "phase": state.phase, "caller_type": "small_model", "agent_id": actor, "model_name": getattr(self.classifier, "model_name", "small-model"), "model_version": result.get("model_version"), "input_visibility": "public", "input_event_ids": [event["event_id"]], "attempt": 1, "success": True, "fallback": False})
                self._event(state, "utterance_classification", "public", source_event_id=event["event_id"], result=result, source=getattr(self.classifier, "model_name", "a-classifier"))
            except Exception as exc: self.store.append_error(state.game_id, {"event_id": event["event_id"], "error": str(exc), "caller_type": "a_classifier"}); self._event(state, "utterance_classification_failed", "public", source_event_id=event["event_id"], error="分类服务暂不可用", fallback=True)

    def _start_day(self, state: GameState) -> None:
        state.night_done.clear(); state.day += 1; state.phase = "day_speak"
        state.day_speakers.clear(); state.day_voted.clear(); state.votes.clear()
        state.revote = False; state.pending_action = None; state.pending = {}

    def _start_hunter(self, state: GameState, actor: int, resume: str) -> None:
        state.hunter_triggered = True
        state.pending.update(hunter_actor=actor, hunter_resume=resume)
        state.phase = "hunter_action"
        self._event(state, "hunter_triggered", [actor, "host", "god"], actor=actor,
                    cause=state.death_reasons.get(actor))
        if state.seat_controllers.get(actor) == "human":
            self._action_for(state, actor)

    def _start_last_words(self, state: GameState, actor: int) -> None:
        state.pending_last_words = actor
        state.phase = "last_words"
        state.pending_action = None
        if state.seat_controllers.get(actor) == "human":
            self._action_for(state, actor)

    def _advance_one_event_locked(self, game_id: str, operation_id: str | None = None) -> dict[str, Any]:
        state = self.games[game_id]
        if state.status not in {"running", "waiting_human"}: return self._response(state, operation_id)
        if state.status == "waiting_human": return self._response(state, operation_id)
        if state.phase == "hunter_action":
            actor = state.pending.get("hunter_actor")
            action = self._consume_action(state, actor)
            if action is None: return self._response(state, operation_id)
            error = check_action(state, actor, action)
            if error:
                state.status = "failed"; self._event(state, "model_failure", [actor, "host", "god"], actor=actor, error=error); return self._response(state, operation_id)
            if action.get("action") == "shoot" and action.get("target") in state.alive:
                target = action["target"]; state.alive.remove(target); state.death_reasons[target] = "hunter_shot"; self._event(state, "hunter_shot", "public", actor=actor, target=target)
            else: self._event(state, "hunter_pass", "public", actor=actor)
            self._record_reason(state, actor, self.store.read(state.game_id)[-1], action, "human" if state.seat_controllers.get(actor) == "human" else getattr(self.agent, "model_name", "stub"))
            resume = state.pending.get("hunter_resume", "after_night")
            state.hunter_processed = True; state.pending_action = None
            if resume == "after_vote":
                self._start_last_words(state, actor)
            else:
                self._start_day(state)
            if self._check_end(state, check_limit=False): return self._response(state, operation_id)
            return self._response(state, operation_id)
        if state.phase == "last_words":
            actor = state.pending_last_words
            action = self._consume_action(state, actor)
            if action is None: return self._response(state, operation_id)
            event = self._event(state, "last_words", "public", actor=actor,
                                text=action.get("text", ""), source="human" if state.seat_controllers.get(actor) == "human" else getattr(self.agent, "model_name", "stub"))
            self._record_reason(state, actor, event, action, event["source"])
            self._classify_speech(state, event, actor)
            state.pending_last_words = None
            state.phase = "night"; state.night_done.clear(); state.pending.clear(); state.revote = False
            self._check_end(state)
            return self._response(state, operation_id)
        if state.phase == "night":
            wolves = [p for p in sorted(state.alive) if state.roles[p] == "wolf" and p not in state.night_done]
            if wolves:
                actor = wolves[0]; action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                if action.get("action") == "kill":
                    state.pending.setdefault("kill_proposals", {})[actor] = action["target"]; event = self._event(state, "wolf_proposal", [p for p in state.alive if state.roles[p] == "wolf"], actor=actor, target=action["target"], source=getattr(self.agent, "model_name", "stub")); self._record_reason(state, actor, event, action, getattr(self.agent, "model_name", "stub"))
                state.night_done.add(actor); return self._response(state, operation_id)
            if "night_kill_target" not in state.pending:
                proposals = state.pending.get("kill_proposals", {})
                targets = list(proposals.values())
                if targets:
                    counts = {p: targets.count(p) for p in set(targets)}; high = max(counts.values()); leaders = sorted(p for p, n in counts.items() if n == high)
                    state.pending["night_kill_target"] = random.Random((state.seed or 0) + state.day).choice(leaders)
            if "seer" not in state.night_done and any(state.roles[p] == "seer" for p in state.alive):
                actor = next(p for p in sorted(state.alive) if state.roles[p] == "seer"); action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                if action.get("action") == "inspect": state.inspection_history.add(action["target"]); self._event(state, "inspection", [actor, "host", "god"], actor=actor, target=action["target"], result="wolf" if state.roles[action["target"]] == "wolf" else "good")
                state.night_done.add("seer"); return self._response(state, operation_id)
            if "witch" not in state.night_done and any(state.roles[p] == "witch" for p in state.alive):
                actor = next(p for p in sorted(state.alive) if state.roles[p] == "witch"); action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                if action.get("action") == "save": state.witch_save_available = False; state.pending["saved"] = True; state.pending["witch_used"] = True; state.witch_self_save_used = state.day == 0 and state.pending.get("night_kill_target") == actor; self._event(state, "witch_save", [actor, "host", "god"], actor=actor, target=state.pending.get("night_kill_target"))
                elif action.get("action") == "poison": state.witch_poison_available = False; state.pending["poison_target"] = action.get("target"); state.pending["witch_used"] = True; self._event(state, "witch_poison", [actor, "host", "god"], actor=actor, target=action.get("target"))
                state.night_done.add("witch"); return self._response(state, operation_id)
            self._resolve_night(state)
            if self._check_end(state, check_limit=False): return self._response(state, operation_id)
            return self._response(state, operation_id)
        if state.phase == "day_speak":
            remaining = [p for p in sorted(state.alive) if p not in state.day_speakers]
            if remaining:
                actor = remaining[0]; action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                state.day_speakers.append(actor); event = self._event(state, "public_speech", "public", actor=actor, text=action.get("text", ""), source="human" if state.seat_controllers.get(actor) == "human" else getattr(self.agent, "model_name", "stub")); self._record_reason(state, actor, event, action, event["source"])
                self._classify_speech(state, event, actor)
                return self._response(state, operation_id)
            state.phase = "day_vote"; return self._response(state, operation_id)
        if state.phase == "day_vote":
            remaining = [p for p in sorted(state.alive) if p not in state.day_voted]
            if remaining:
                actor = remaining[0]; action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                state.day_voted.add(actor); target = action.get("target") if action.get("action") == "vote" else None; valid = target is None or target in state.alive and target != actor
                if valid and target is not None: state.votes[actor] = target
                self._event(state, "vote", "public", actor=actor, target=target, valid=valid, source="human" if state.seat_controllers.get(actor) == "human" else getattr(self.agent, "model_name", "stub")); return self._response(state, operation_id)
            target, counts = tally_votes(state.votes); self._event(state, "vote_tally", "public", counts=counts, target=target, revote=state.revote)
            if target is None and not state.revote:
                if counts:
                    state.pending["revote_candidates"] = sorted(p for p, n in counts.items() if n == max(counts.values())); state.revote = True; state.day_voted.clear(); state.votes.clear(); return self._response(state, operation_id)
                self._event(state, "no_valid_votes", "public", resolution="no_exile")
            elif target in state.alive:
                state.alive.remove(target); state.death_reasons[target] = "exile"; self._event(state, "exile", "public", target=target)
                if state.roles.get(target) == "hunter" and not state.hunter_processed:
                    self._start_hunter(state, target, "after_vote")
                else:
                    self._start_last_words(state, target)
                return self._response(state, operation_id)
            if self._check_end(state): return self._response(state, operation_id)
            state.phase = "night"; state.night_done.clear(); state.pending.clear(); state.revote = False; return self._response(state, operation_id)
        return self._response(state, operation_id)

    def advance_one_event(self, game_id: str, operation_id: str | None = None, expected_cursor: int | None = None) -> dict[str, Any]:
        with self._locks[game_id]:
            state = self.games[game_id]
            if operation_id and operation_id in state.operation_results: return self.get_operation(game_id, operation_id)["result"]
            if expected_cursor is not None and expected_cursor != state.event_id: return {"game_id": game_id, "operation_id": operation_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            state.operation_start_event_id = state.event_id; response = self._advance_one_event_locked(game_id, operation_id)
            if operation_id: state.operation_results[operation_id] = response
            self.store.write_state(game_id, state.to_dict()); return response

    def _response(self, state: GameState, operation_id: str | None = None) -> dict[str, Any]:
        before = state.operation_start_event_id if state.operation_start_event_id is not None else state.event_id; state.operation_start_event_id = None
        if state.pause_requested and state.status in {"running", "waiting_model", "waiting_human"}: state.status = "paused"; state.pause_requested = False; self._event(state, "game_paused", "host")
        new_events = list(range(before + 1, state.event_id + 1)); summary = self.get_metrics(state.game_id); self.store.write_state(state.game_id, state.to_dict())
        return {"game_id": state.game_id, "operation_id": operation_id, "new_event": state.event_id if new_events else None, "new_events": new_events, "cursor": state.event_id, "phase": state.phase, "status": state.status, "pending_action": state.pending_action, "summary": summary}

    def get_status(self, game_id: str) -> dict[str, Any]:
        s = self.games[game_id]; return {"game_id": game_id, "status": s.status, "pause_requested": s.pause_requested, "phase": s.phase, "day": s.day, "cursor": s.event_id, "winner": s.winner, "pending_action": s.pending_action, "seat_controllers": s.seat_controllers, "restricted": self.human_restricted(game_id), "human_player_id": self.human_seat(game_id)}

    def get_player_identity(self, game_id: str, player_id: int) -> dict[str, Any]:
        s = self.games[game_id]
        if s.seat_controllers.get(player_id) != "human" or self.human_seat(game_id) != player_id:
            raise ValueError("seat_is_not_human")
        result = {"game_id": game_id, "player_id": player_id, "role": s.roles[player_id],
                  "alive": player_id in s.alive,
                  "legal_actions": legal_actions_for(s, player_id) if s.pending_action and s.pending_action.get("actor") == player_id else []}
        if s.roles[player_id] == "wolf":
            result["wolf_teammates"] = [p for p in sorted(s.alive) if p != player_id and s.roles[p] == "wolf"]
        return result

    def human_seat(self, game_id: str) -> int | None:
        seats = [p for p, controller in self.games[game_id].seat_controllers.items() if controller == "human"]
        if len(seats) > 1:
            raise PermissionError("multi-human games require a separate authorization scheme")
        return seats[0] if seats else None

    def human_restricted(self, game_id: str) -> bool:
        return any(v == "human" for v in self.games[game_id].seat_controllers.values()) and self.games[game_id].status not in {"finished", "incomplete"}

    def get_operation(self, game_id: str, operation_id: str) -> dict[str, Any]:
        s = self.games[game_id]
        result = s.operation_results.get(operation_id)
        if result and self.human_restricted(game_id):
            result = {k: v for k, v in result.items() if k in {"game_id", "operation_id", "new_event", "new_events", "cursor", "phase", "status", "pending_action"}}
            result["summary"] = self.get_metrics(game_id)
        return {"game_id": game_id, "operation_id": operation_id, "status": "completed" if result else "unknown", "result": result}

    def get_events(self, game_id: str, after_event_id: int = 0, view: str = "god", player_id: int | None = None) -> list[dict[str, Any]]:
        s = self.games[game_id]; events = [e for e in self.store.read(game_id) if e["event_id"] > after_event_id]
        if self.human_restricted(game_id):
            actor = self.human_seat(game_id)
            requested = player_id if player_id is not None else (int(view) if str(view).isdigit() else None)
            if requested is not None and requested != actor:
                raise PermissionError("cannot read another seat during a human game")
            if requested == actor:
                return [e for e in events if e.get("visibility") == "public" or
                        isinstance(e.get("visibility"), list) and actor in e["visibility"]]
            # god/host are untrusted display choices, not permission grants.
            return [e for e in events if e.get("visibility") == "public"]
        viewer = player_id if player_id is not None else (int(view) if str(view).isdigit() else None)
        if view == "god": return events
        if viewer is None: return [e for e in events if e.get("visibility") == "public"]
        return [e for e in events if e.get("visibility") == "public" or (isinstance(e.get("visibility"), list) and viewer in e["visibility"])]

    def get_metrics(self, game_id: str) -> dict[str, Any]:
        if self.human_restricted(game_id): return {"game_id": game_id, "restricted": True}
        return summarize(self.store, game_id)

    def replay_game(self, game_id: str) -> list[dict[str, Any]]:
        if self.human_restricted(game_id): raise PermissionError("human game replay is unavailable until the game ends")
        return self.store.replay(game_id)
