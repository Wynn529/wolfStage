from __future__ import annotations

import logging
import os
import random
import threading
import uuid
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from .agents import DeepSeekAgent, SeatAgentRouter, StubAgent, build_context, legal_actions_for
from .deepseek_client import ModelOutputError
from .events import EventStore
from .metrics import summarize
from .provider_client import is_request_timeout
from .rules import GameState, RULE_VERSION, assign_roles, check_action, is_valid_seed, tally_votes, winner
from .seat_config import MatchConfig
from .seat_router import EngineSeatAgent


logger = logging.getLogger(__name__)


class InvalidGameRequest(ValueError):
    """A caller supplied invalid game or control parameters."""


class GameEngine:
    """十人规则状态机，统一驱动 AI 席位和真人席位。"""

    def __init__(self, runs_dir: str = "runs", agent: Any | None = None, classifier: Any | None = None, agents: dict[str, Any] | None = None):
        self.store = EventStore(runs_dir)
        self.games: dict[str, GameState] = {}
        self.unavailable_games: set[str] = set()
        base = agent or (DeepSeekAgent() if os.getenv("DEEPSEEK_API_KEY") else StubAgent(classifier))
        self.agent = SeatAgentRouter(base, agents) if agents else base
        self.configured_agent = EngineSeatAgent()
        self.classifier = classifier
        self._locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._checkpoint_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
        self._agent_lock = threading.RLock()
        self._staged_events: dict[str, list[dict[str, Any]]] = {}
        self._step_operations: dict[str, str | None] = {}
        self.max_player_calls = int(os.getenv("B_MAX_PLAYER_CALLS", "200"))
        self.max_small_model_calls = int(os.getenv("B_MAX_SMALL_MODEL_CALLS", "200"))
        for game_id in self.store.list_games():
            try:
                logged_cursor = self.store.validate_saved_log(game_id)
                recovered = self.store.recover_events(game_id)
                saved = recovered if recovered is not None else self.store.read_state(game_id)
                if not isinstance(saved, dict) or saved.get("game_id") != game_id:
                    raise ValueError("saved state does not match game directory")
                if recovered is not None:
                    logged_cursor = self.store.validate_saved_log(game_id)
                cursor = saved.get("event_id", 0)
                if type(cursor) is not int or not 0 <= cursor <= logged_cursor:
                    raise ValueError("saved state cursor is outside its event log")
                self.games[game_id] = GameState.from_dict(saved)
                self._recover_interrupted_retry(self.games[game_id])
                self._recover_model_step(self.games[game_id])
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                self.games.pop(game_id, None)
                self.unavailable_games.add(game_id)
                logger.warning("Game %s could not be recovered (%s); leaving its records in place",
                               game_id, type(exc).__name__)

    def _event(self, state: GameState, typ: str, visibility: Any = "public", **kwargs: Any) -> dict[str, Any]:
        state.event_id += 1
        source = kwargs.pop("source", "rules")
        event = {"game_id": state.game_id, "event_id": state.event_id, "timestamp": datetime.now(timezone.utc).isoformat(), "phase": state.phase, "day": state.day, "type": typ, "visibility": visibility, "rules_version": state.rules_version, "source": source, **kwargs}
        staged = self._staged_events.get(state.game_id)
        if staged is None:
            self.store.append(state.game_id, event)
        else:
            staged.append(event)
        return event

    @contextmanager
    def _stage_events(self, game_id: str):
        events: list[dict[str, Any]] = []
        self._staged_events[game_id] = events
        try:
            yield events
        finally:
            del self._staged_events[game_id]

    def create_game(self, seed: int | None = None, role_config: dict[int, str] | None = None, *, seat_controllers: dict[int, str] | None = None, seat_model_configs: dict[int, dict[str, Any]] | None = None, player_count: int = 10, match_config: MatchConfig | None = None) -> dict[str, Any]:
        if not is_valid_seed(seed):
            raise InvalidGameRequest("seed must be an integer or null")
        game_id = uuid.uuid4().hex[:12]
        roles = assign_roles(seed, role_config, player_count=player_count)
        if match_config is not None:
            from dataclasses import asdict
            profiles = {p.profile_id: p for p in match_config.profiles}
            seat_controllers = {s.seat_id: s.control_type for s in match_config.seats}
            seat_model_configs = {s.seat_id: asdict(profiles[s.model_profile_id]) for s in match_config.seats if s.control_type == "ai"}
            if match_config.human_role:
                human = next(s.seat_id for s in match_config.seats if s.control_type == "human")
                other = next(p for p, role in roles.items() if role == match_config.human_role)
                roles[human], roles[other] = roles[other], roles[human]
        controllers = {p: "ai" for p in roles}
        if seat_controllers:
            controllers.update({int(k): v for k, v in seat_controllers.items()})
        if sum(v == "human" for v in controllers.values()) > 1:
            raise ValueError("this local mode supports one human seat only")
        if any(v not in {"ai", "human"} for v in controllers.values()):
            raise ValueError("seat_controllers values must be ai or human")
        if set(controllers) != set(roles) or sum(v == "human" for v in controllers.values()) > 1:
            raise ValueError("controllers must match seats with at most one human")
        state = GameState(game_id, seed, roles, seat_controllers=controllers, seat_model_configs=seat_model_configs or {}, rules_version=RULE_VERSION if len(roles) == 10 else "rules-v1.0")
        self.games[game_id] = state
        self._event(state, "game_created", "host", roles=state.roles, alive=sorted(state.alive), seat_controllers=state.seat_controllers, model_configs=state.seat_model_configs)
        self.store.write_state(game_id, state.to_dict())
        return {"game_id": game_id, "status": state.status, "phase": state.phase, "rules_version": state.rules_version, "players": sorted(state.roles), "seat_controllers": state.seat_controllers}

    @staticmethod
    def _validate_control(operation_id: str | None, expected_cursor: int | None) -> None:
        if operation_id is not None and (not isinstance(operation_id, str) or not operation_id.strip()):
            raise InvalidGameRequest("operation_id must be a non-empty string")
        if expected_cursor is not None and (type(expected_cursor) is not int or expected_cursor < 0):
            raise InvalidGameRequest("expected_cursor must be a non-negative integer")

    def pause_game(self, game_id: str) -> dict[str, Any]:
        with self._checkpoint_locks[game_id]:
            state = self.games[game_id]
            checkpoint = state.pending.get("ordinary_model_step")
            if (state.status == "waiting_model" and checkpoint and "outcome" not in checkpoint
                    and not state.pending.get("model_retry_operation")):
                state.pause_requested = True
                self.store.write_state(game_id, state.to_dict())
                return {"game_id": game_id, "status": state.status, "pause_requested": True}
        with self._locks[game_id]:
            state = self._recover_pending_state(game_id)
            if state.status in {"waiting_model", "waiting_human"}:
                state.pause_requested = True; self.store.write_state(game_id, state.to_dict())
                return {"game_id": game_id, "status": state.status, "pause_requested": True}
            if state.status == "running":
                self._commit_control_transition(state, "paused", "game_paused")
            return {"game_id": game_id, "status": state.status}

    def resume_game(self, game_id: str) -> dict[str, Any]:
        with self._locks[game_id]:
            state = self._recover_pending_state(game_id)
            if state.status == "paused":
                self._commit_control_transition(state, "running", "game_resumed")
            elif state.pause_requested:
                state.pause_requested = False
                self.store.write_state(game_id, state.to_dict())
            return {"game_id": game_id, "status": state.status}

    def _commit_control_transition(self, state: GameState, status: str, event_type: str) -> None:
        settled = deepcopy(state)
        settled.status = status
        settled.pause_requested = False
        with self._stage_events(state.game_id) as events:
            self._event(settled, event_type, "host")
        self.store.commit_events(state.game_id, settled.to_dict(), events)
        state.__dict__.update(settled.__dict__)

    def _call_agent(self, state: GameState, actor: int) -> dict[str, Any]:
        game_id = state.game_id
        checkpoint = state.pending.get("ordinary_model_step")
        if checkpoint and "outcome" in checkpoint:
            outcome = checkpoint["outcome"]
            if outcome["call"]["agent_id"] != actor:
                raise ValueError(f"Model checkpoint seat mismatch in game {game_id}")
            self._record_model_call(game_id, outcome["call"])
            return self._finish_model_action(state, actor, outcome)
        manual_retry = state.pending.pop("model_timeout", False)
        durable = game_id in self._step_operations
        events = self.store.read(state.game_id); role = state.roles[actor]; context = build_context(state, events, actor, role)
        config = state.seat_model_configs.get(actor, {})
        agent = self.configured_agent if config.get("profile_id") else self.agent
        base = {"game_id": state.game_id, "event_id": state.event_id + 1, "phase": state.phase, "caller_type": "player", "agent_id": actor, "seat_id": actor, "model_name": config.get("model_name") or config.get("model") or getattr(self.agent, "model_name", "unknown"), "model_version": config.get("model_version") or getattr(self.agent, "model_version", "unknown"), "prompt_version": config.get("prompt_version", f"{role}-v1"), "input_visibility": actor, "input_event_ids": [e["event_id"] for e in context["events"]], "fallback": False}
        if getattr(agent, "action_protocol_version", None):
            base["prompt_version"] += "+" + agent.action_protocol_version
        used = sum(1 for c in self.store.read_calls(state.game_id) if c.get("caller_type") == "player")
        if used >= self.max_player_calls:
            state.status = "failed"; self._event(state, "budget_exceeded", [actor, "host", "god"], actor=actor, caller_type="player", limit=self.max_player_calls)
            return {"action": "pass", "target": None, "text": "", "reason": "player call limit exceeded"}
        for attempt in (1,) if manual_retry else (1, 2):
            timed_out = False
            state.status = "waiting_model"; metadata: dict[str, Any] = {}
            call_id = f"{game_id}:player:{state.event_id + 1}:{state.phase}:{actor}:{attempt}"
            if durable:
                reserved = dict(base, call_id=call_id, attempt=attempt)
                self._checkpoint_model_step(state, reserved)
            try:
                with self._agent_lock:
                    try:
                        action = agent.act(state, actor, context)
                    finally:
                        metadata = dict(getattr(agent, "last_metadata", {}) or {})
                error = check_action(state, actor, action); retryable = error is not None; stage = "action_validation" if error else None
            except Exception as exc:
                error_metadata = getattr(exc, "metadata", {}) or {}
                action = {"action": "pass", "target": None, "text": "", "reason": str(exc)}; metadata.update(error_metadata); error = str(exc); retryable = isinstance(exc, ModelOutputError); stage = "output_parsing" if retryable else "request"
                timed_out = stage == "request" and (is_request_timeout(exc) or error_metadata.get("request_timeout") is True)
                if timed_out:
                    error = "model request timed out"
            call = dict(base, attempt=attempt, success=error is None, validation_error=error, failure_stage=stage, latency_ms=metadata.get("latency_ms"), input_tokens=metadata.get("input_tokens"), output_tokens=metadata.get("output_tokens"), model_name=metadata.get("model_name", base["model_name"]), model_version=metadata.get("model_version", base["model_version"]))
            call["call_id"] = call_id
            for key in ("provider", "model_profile_id", "requested_model", "http_status", "provider_error_code", "prompt_hash", "response_id", "returned_model", "request_evidence", "request_sha256"):
                if key in metadata: call[key] = metadata[key]
            retry_validation = attempt == 1 and retryable and not manual_retry and used + attempt < self.max_player_calls
            outcome = {"action": action, "call": call, "timeout": timed_out}
            if durable and not retry_validation:
                self._checkpoint_model_step(state, call, outcome)
            if durable:
                self._record_model_call(game_id, call)
            else:
                self.store.append_call(game_id, call)
            if error is not None:
                self.store.append_error(game_id, {"event_id": state.event_id + 1, "actor": actor, "attempt": attempt, "error": error, "failure_stage": stage})
            if retry_validation:
                context = dict(context, validation_error=error, validation_feedback={"previous_action": action, "error": error}); continue
            return self._finish_model_action(state, actor, outcome)
        return {"action": "pass", "target": None, "text": "", "reason": "unreachable"}

    def _checkpoint_model_step(self, state: GameState, call: dict[str, Any],
                               outcome: dict[str, Any] | None = None) -> None:
        game_id = state.game_id
        with self._checkpoint_locks[game_id]:
            current = self.games[game_id]
            state.pause_requested = state.pause_requested or current.pause_requested
            checkpoint = {"operation_id": self._step_operations[game_id], "call": call}
            if outcome is not None:
                checkpoint["outcome"] = outcome
            state.pending["ordinary_model_step"] = checkpoint
            self.store.write_state(game_id, state.to_dict())
            current.__dict__.update(deepcopy(state).__dict__)

    def _record_model_call(self, game_id: str, call: dict[str, Any]) -> bool:
        if any(item.get("call_id") == call["call_id"] for item in self.store.read_calls(game_id)):
            return False
        self.store.append_call(game_id, call, atomic=True)
        return True

    def _finish_model_action(self, state: GameState, actor: int, outcome: dict[str, Any]) -> dict[str, Any]:
        state.pending.pop("ordinary_model_step", None)
        state.pending.pop("model_retry_operation", None)
        error = outcome["call"]["validation_error"]
        if error is None:
            state.status = "waiting_model" if state.pause_requested else "running"
            return outcome["action"]
        timed_out = outcome["timeout"]
        if timed_out:
            state.pending["model_timeout"] = True
        state.status = "failed"
        self._event(state, "model_failure", [actor], actor=actor, error=error, request_timeout=timed_out)
        return {"action": "pass", "target": None, "text": "", "reason": error}

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
        self._validate_control(operation_id, expected_cursor)
        if action_id is not None and (not isinstance(action_id, str) or not action_id.strip()):
            raise InvalidGameRequest("action_id must be a non-empty string")
        with self._locks[game_id]:
            state = self._recover_pending_state(game_id)
            if operation_id and operation_id in state.operation_results:
                return state.operation_results[operation_id]
            if state.seat_controllers.get(actor) != "human": return {"game_id": game_id, "status": "rejected", "error": "seat_is_not_human"}
            if expected_cursor is not None and expected_cursor != state.event_id: return {"game_id": game_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            pending = state.pending_action or {}
            if pending.get("actor") != actor or (action_id and pending.get("action_id") != action_id): return {"game_id": game_id, "status": "rejected", "error": "pending_action_mismatch"}
            error = check_action(state, actor, action)
            if error: self.store.append_error(game_id, {"actor": actor, "error": error, "source": "human"}); return {"game_id": game_id, "status": "rejected", "error": error}
            settled = deepcopy(state)
            settled.operation_start_event_id = state.event_id
            settled.pending["submitted_action"] = action
            settled.pending_action = None
            settled.status = "running"
            with self._stage_events(game_id) as events:
                self._event(settled, "human_action_submitted", [actor, "host", "god"],
                            actor=actor, action=action, source="human")
                if settled.phase == "night" and settled.roles.get(actor) == "wolf":
                    discussion = action.get("text") or action.get("reason")
                    if discussion:
                        wolves = [p for p in settled.alive if settled.roles.get(p) == "wolf"]
                        self._event(settled, "wolf_discussion", wolves + ["host", "god"], actor=actor,
                                    text=discussion, source="human")
                result = self._advance_one_event_locked(game_id, operation_id, state=settled)
            return self._publish_step(state, settled, events, result, operation_id)

    def submit_wolf_discussion(self, game_id: str, actor: int, text: str, *, expected_cursor: int | None = None) -> dict[str, Any]:
        self._validate_control(None, expected_cursor)
        with self._locks[game_id]:
            state = self._recover_pending_state(game_id)
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
        action = action if action is not None else self._action_for(state, actor)
        return None if state.status == "failed" else action

    def _source(self, state, actor):
        if state.seat_controllers.get(actor) == "human":
            return "human"
        return state.seat_model_configs.get(actor, {}).get("model", getattr(self.agent, "model_name", "stub"))

    def _record_reason(self, state: GameState, actor: int, event: dict[str, Any], action: dict[str, Any], source: str) -> None:
        if action.get("reason") and state.seat_controllers.get(actor) != "human":
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
            if len(leaders) > 1 or len(set(targets)) > 1: self._event(state, "wolf_negotiation", [p for p in state.alive if state.roles[p] == "wolf"], proposals=proposals, resolved_target=kill_target, resolution="seeded_rule" if len(state.roles) == 6 else "majority_seeded_tie")
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
        game_id, event_id = state.game_id, event["event_id"]
        digest = sha256(event["text"].encode("utf-8")).hexdigest()
        call_id = f"{game_id}:small:{event_id}:{state.phase}:{actor}:{digest}"
        checkpoint = self.store.read_classification_checkpoint(game_id)
        if checkpoint is None or checkpoint["call_id"] != call_id:
            if self.classifier is None:
                return
            model_name = getattr(self.classifier, "model_name", "small-model")
            try:
                result = self.classifier(game_id, event_id, actor, event["text"])
                if not isinstance(result, dict):
                    raise ValueError("classification result must be an object")
            except Exception as exc:
                checkpoint = {"call_id": call_id, "model_name": model_name, "error": str(exc)}
            else:
                checkpoint = {"call_id": call_id, "model_name": model_name, "result": result,
                              "source": getattr(self.classifier, "model_name", "a-classifier")}
            self.store.write_classification_checkpoint(game_id, checkpoint)
        call = {"call_id": call_id, "game_id": game_id, "event_id": event_id, "phase": state.phase,
                "caller_type": "small_model", "agent_id": actor, "attempt": 1, "fallback": False}
        if "error" in checkpoint:
            error = checkpoint["error"]
            if self._record_model_call(game_id, dict(call, success=False, validation_error=error)):
                self.store.append_error(game_id, {"event_id": event_id, "error": error, "caller_type": "a_classifier"})
            state.status = "failed"
            self._event(state, "utterance_classification_failed", "public", source_event_id=event_id,
                        error="分类服务暂不可用", fallback=False)
            return
        result = checkpoint["result"]
        self._record_model_call(game_id, dict(call, success=True, model_name=checkpoint["model_name"],
                                             model_version=result.get("model_version"),
                                             input_visibility="public", input_event_ids=[event_id]))
        state.classification_by_event[event_id] = result
        self._event(state, "utterance_classification", "public", source_event_id=event_id,
                    result=result, source=checkpoint["source"])

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

    def _advance_one_event_locked(self, game_id: str, operation_id: str | None = None, *, state: GameState | None = None) -> dict[str, Any]:
        if state is None:
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
                target = action["target"]; state.alive.remove(target); state.death_reasons[target] = "hunter_shot"; event = self._event(state, "hunter_shot", "public", actor=actor, target=target)
            else: event = self._event(state, "hunter_pass", "public", actor=actor)
            self._record_reason(state, actor, event, action, "human" if state.seat_controllers.get(actor) == "human" else self._source(state, actor))
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
                                text=action.get("text", ""), source="human" if state.seat_controllers.get(actor) == "human" else self._source(state, actor))
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
                    state.pending.setdefault("kill_proposals", {})[actor] = action["target"]; event = self._event(state, "wolf_proposal", [p for p in state.alive if state.roles[p] == "wolf"], actor=actor, target=action["target"], source=self._source(state, actor)); self._record_reason(state, actor, event, action, self._source(state, actor))
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
            if state.phase != "hunter_action" and self._check_end(state, check_limit=False): return self._response(state, operation_id)
            return self._response(state, operation_id)
        if state.phase == "day_speak":
            remaining = [p for p in sorted(state.alive) if p not in state.day_speakers]
            if remaining:
                actor = remaining[0]; action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                state.day_speakers.append(actor); event = self._event(state, "public_speech", "public", actor=actor, text=action.get("text", ""), source="human" if state.seat_controllers.get(actor) == "human" else self._source(state, actor)); self._record_reason(state, actor, event, action, event["source"])
                self._classify_speech(state, event, actor)
                return self._response(state, operation_id)
            state.phase = "day_vote"
            self._event(state, "vote_open")
            return self._response(state, operation_id)
        if state.phase == "day_vote":
            remaining = [p for p in sorted(state.alive) if p not in state.day_voted]
            if remaining:
                actor = remaining[0]; action = self._consume_action(state, actor)
                if action is None: return self._response(state, operation_id)
                state.day_voted.add(actor)
                state.votes[actor] = action.get("target") if action.get("action") == "vote" else None
                self._event(state, "vote_received")
                return self._response(state, operation_id)
            target, counts = tally_votes(state.votes)
            should_revote = target is None and bool(counts) and not state.revote and len(state.roles) <= 6
            resolution = "exile" if target is not None else "revote" if should_revote else "no_exile"
            self._event(state, "vote_tally", counts={p: counts.get(p, 0) for p in sorted(state.alive)},
                        target=target, revote=state.revote, resolution=resolution,
                        abstentions=len(state.day_voted) - sum(counts.values()))
            if should_revote:
                state.pending["revote_candidates"] = sorted(p for p, n in counts.items() if n == max(counts.values()))
                state.revote = True; state.day_voted.clear(); state.votes.clear()
                self._event(state, "vote_open", revote=True)
                return self._response(state, operation_id)
            if target is None and not counts and not state.revote and len(state.roles) <= 6:
                self._event(state, "no_valid_votes", "public", resolution="no_exile")
            elif target in state.alive:
                state.alive.remove(target); state.death_reasons[target] = "exile"; self._event(state, "exile", "public", target=target)
                if len(state.roles) == 6:
                    self._event(state, "last_words", [target, "host", "god"], actor=target, text="")
                    if not self._check_end(state):
                        state.phase = "night"; state.night_done.clear(); state.pending.clear(); state.revote = False
                    return self._response(state, operation_id)
                if state.roles.get(target) == "hunter" and not state.hunter_processed:
                    self._start_hunter(state, target, "after_vote")
                else:
                    self._start_last_words(state, target)
                return self._response(state, operation_id)
            if self._check_end(state): return self._response(state, operation_id)
            state.phase = "night"; state.night_done.clear(); state.pending.clear(); state.revote = False; return self._response(state, operation_id)
        return self._response(state, operation_id)

    def advance_one_event(self, game_id: str, operation_id: str | None = None, expected_cursor: int | None = None, *, retry_model: bool = False) -> dict[str, Any]:
        self._validate_control(operation_id, expected_cursor)
        with self._locks[game_id]:
            state = self._recover_pending_state(game_id)
            if operation_id and operation_id in state.operation_results: return self.get_operation(game_id, operation_id)["result"]
            if expected_cursor is not None and expected_cursor != state.event_id: return {"game_id": game_id, "operation_id": operation_id, "status": "conflict", "error": "cursor_conflict", "cursor": state.event_id}
            if retry_model:
                if not operation_id or expected_cursor is None or not self._can_retry_model(state):
                    return {"game_id": game_id, "operation_id": operation_id, "status": "rejected", "error": "no_retryable_model_timeout"}
                self._start_model_retry(state, operation_id)
                return self._commit_step(state, operation_id, retry_model=True)
            if state.status == "running":
                return self._commit_step(state, operation_id)
            state.operation_start_event_id = state.event_id
            response = self._advance_one_event_locked(game_id, operation_id)
            if operation_id: state.operation_results[operation_id] = response
            self.store.write_state(game_id, state.to_dict()); return response

    def _recover_pending_state(self, game_id: str) -> GameState:
        state = self.games[game_id]
        recovered = self.store.recover_events(game_id)
        if recovered is None and state.pending.get("ordinary_model_step"):
            recovered = self.store.read_state(game_id)
        if recovered is not None:
            state.__dict__.update(GameState.from_dict(recovered).__dict__)
        self._recover_interrupted_retry(state)
        self._recover_model_step(state)
        return state

    def _start_model_retry(self, state: GameState, operation_id: str) -> None:
        started = deepcopy(state)
        started.operation_start_event_id = state.event_id
        started.pause_requested = False
        started.pending.pop("model_retry_interrupted", None)
        started.pending.update(model_timeout=True, model_retry_operation=operation_id)
        started.status = "waiting_model"
        with self._stage_events(state.game_id) as events:
            self._event(started, "model_retry_started")
        self.store.commit_events(state.game_id, started.to_dict(), events)
        state.__dict__.update(started.__dict__)

    def _recover_interrupted_retry(self, state: GameState) -> None:
        operation_id = state.pending.get("model_retry_operation")
        if state.status != "waiting_model" or not operation_id or state.pending.get("ordinary_model_step"):
            return
        self._count_interrupted_retry(state)
        self._finish_interrupted_model_step(state, operation_id, "model_retry_interrupted")

    def _recover_model_step(self, state: GameState) -> None:
        checkpoint = state.pending.get("ordinary_model_step")
        if state.status != "waiting_model" or not checkpoint:
            return
        operation_id = checkpoint["operation_id"]
        if "outcome" in checkpoint:
            self._commit_step(state, operation_id)
            return
        call = dict(checkpoint["call"], success=False, failure_stage="interrupted",
                    input_tokens=None, output_tokens=None)
        self._record_model_call(state.game_id, call)
        event_type = "model_retry_interrupted" if state.pending.get("model_retry_operation") else "model_step_interrupted"
        self._finish_interrupted_model_step(state, operation_id, event_type)

    def _finish_interrupted_model_step(self, state: GameState, operation_id: str | None,
                                       event_type: str) -> None:
        interrupted = deepcopy(state)
        interrupted.pending.pop("model_retry_operation", None)
        interrupted.pending.pop("ordinary_model_step", None)
        interrupted.pending["model_timeout"] = True
        interrupted.pending["model_retry_interrupted"] = True
        interrupted.pause_requested = False
        interrupted.status = "failed"
        with self._stage_events(state.game_id) as events:
            self._event(interrupted, event_type)
            result = self._response(interrupted, operation_id)
        result["error"] = event_type
        self._publish_step(state, interrupted, events, result, operation_id)

    def _count_interrupted_retry(self, state: GameState) -> None:
        # This slot was reserved before calling the provider. Keep it counted
        # unless the actual attempt is already recorded.
        event_id = state.event_id + 1
        calls = [call for call in self.store.read_calls(state.game_id)
                 if call.get("caller_type") == "player"]
        if any(call.get("event_id") == event_id for call in calls):
            return
        previous = calls[-1] if calls else {}
        self.store.append_call(state.game_id, {
            "call_id": f"{state.game_id}:interrupted-retry:{state.event_id}",
            "game_id": state.game_id, "event_id": event_id, "phase": state.phase,
            "caller_type": "player", "agent_id": previous.get("agent_id"),
            "model_name": previous.get("model_name"), "attempt": 1,
            "success": False, "failure_stage": "interrupted",
            "input_tokens": None, "output_tokens": None,
        }, atomic=True)

    def _commit_step(self, state: GameState, operation_id: str | None, *, retry_model: bool = False) -> dict[str, Any]:
        game_id = state.game_id
        settled = deepcopy(state)
        if retry_model or state.pending.get("ordinary_model_step"):
            settled.status = "running"
        else:
            settled.operation_start_event_id = state.event_id
        with self._stage_events(game_id) as events:
            self._step_operations[game_id] = operation_id
            try:
                response = self._advance_one_event_locked(game_id, operation_id, state=settled)
            finally:
                self._step_operations.pop(game_id, None)
        settled.pending.pop("model_retry_operation", None)
        return self._publish_step(state, settled, events, response, operation_id)

    def _publish_step(self, state: GameState, settled: GameState, events: list[dict[str, Any]],
                      response: dict[str, Any], operation_id: str | None) -> dict[str, Any]:
        if operation_id:
            settled.operation_results[operation_id] = response
        self.store.commit_events(state.game_id, settled.to_dict(), events, response["summary"])
        state.__dict__.update(settled.__dict__)
        return response

    def _response(self, state: GameState, operation_id: str | None = None) -> dict[str, Any]:
        before = state.operation_start_event_id if state.operation_start_event_id is not None else state.event_id; state.operation_start_event_id = None
        if state.pause_requested and state.status in {"running", "waiting_model", "waiting_human"}: state.status = "paused"; state.pause_requested = False; self._event(state, "game_paused", "host")
        new_events = list(range(before + 1, state.event_id + 1))
        staged = self._staged_events.get(state.game_id)
        if staged is None:
            summary = self.get_metrics(state.game_id)
            self.store.write_state(state.game_id, state.to_dict())
        elif any(controller == "human" for controller in state.seat_controllers.values()) and state.status not in {"finished", "incomplete"}:
            summary = {"game_id": state.game_id, "restricted": True}
        else:
            summary = summarize(self.store, state.game_id, staged_events=staged)
        return {"game_id": state.game_id, "operation_id": operation_id, "new_event": state.event_id if new_events else None, "new_events": new_events, "cursor": state.event_id, "phase": state.phase, "status": state.status, "pending_action": state.pending_action, "summary": summary}

    def _can_retry_model(self, state: GameState) -> bool:
        if state.status != "failed":
            return False
        calls = [call for call in self.store.read_calls(state.game_id)
                 if call.get("caller_type") == "player"]
        if len(calls) >= self.max_player_calls:
            return False
        if state.pending.get("model_timeout") is True:
            return True
        # Older snapshots lack the marker. Require the matching final failed
        # request and explicit timeout evidence from the recorded call.
        if not calls:
            return False
        call = calls[-1]
        return (call.get("event_id") == state.event_id and call.get("phase") == state.phase
                and call.get("failure_stage") == "request" and call.get("success") is False
                and (call.get("http_status") in {408, 504}
                     or str(call.get("validation_error")).casefold() in {
                         "timed out", "the read operation timed out", "<urlopen error timed out>"}))

    def get_status(self, game_id: str) -> dict[str, Any]:
        s = self.games[game_id]; return {"game_id": game_id, "status": s.status, "pause_requested": s.pause_requested, "phase": s.phase, "day": s.day, "cursor": s.event_id, "last_event_id": s.event_id, "in_flight": s.status == "waiting_model", "winner": s.winner, "pending_action": s.pending_action, "seat_controllers": s.seat_controllers, "restricted": self.human_restricted(game_id), "human_player_id": self.human_seat(game_id), "can_retry_model": self._can_retry_model(s)}

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
            result = {k: v for k, v in result.items() if k in {"game_id", "operation_id", "new_event", "new_events", "cursor", "phase", "status", "pending_action", "error"}}
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
