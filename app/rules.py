from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "rules-v2.0-ten"
LEGACY_RULE_VERSION = "rules-v1.0"
TEN_PLAYERS = tuple(range(1, 11))
PLAYERS = TEN_PLAYERS
TEN_ROLE_COUNTS = {"wolf": 3, "seer": 1, "witch": 1, "hunter": 1, "villager": 4}


@dataclass
class GameState:
    game_id: str
    seed: int | None
    roles: dict[int, str]
    alive: set[int] | None = None
    phase: str = "night"
    day: int = 0
    status: str = "running"
    event_id: int = 0
    pending: dict[str, Any] = field(default_factory=dict)
    votes: dict[int, int | None] = field(default_factory=dict)
    revote: bool = False
    winner: str | None = None
    night_done: set[Any] = field(default_factory=set)
    day_speakers: list[int] = field(default_factory=list)
    day_voted: set[int] = field(default_factory=set)
    classification_by_event: dict[int, dict[str, Any]] = field(default_factory=dict)
    operation_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    pause_requested: bool = False
    operation_start_event_id: int | None = None
    rules_version: str = RULE_VERSION
    seat_controllers: dict[int, str] = field(default_factory=dict)
    seat_model_configs: dict[int, dict[str, Any]] = field(default_factory=dict)
    witch_save_available: bool = True
    witch_poison_available: bool = True
    witch_self_save_used: bool = False
    inspection_history: set[int] = field(default_factory=set)
    death_reasons: dict[int, str] = field(default_factory=dict)
    hunter_triggered: bool = False
    hunter_processed: bool = False
    pending_last_words: int | None = None
    pending_action: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.roles = {int(k): v for k, v in self.roles.items()}
        self.alive = set(self.roles) if self.alive is None else set(self.alive)
        self.seat_controllers = ({p: "ai" for p in self.roles} if not self.seat_controllers else {int(k): v for k, v in self.seat_controllers.items()})
        self.seat_model_configs = {int(k): dict(v) for k, v in self.seat_model_configs.items()}
        if len(self.roles) <= 6 and self.rules_version == RULE_VERSION:
            self.rules_version = LEGACY_RULE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id, "seed": self.seed, "roles": self.roles, "alive": sorted(self.alive),
            "phase": self.phase, "day": self.day, "status": self.status, "event_id": self.event_id,
            "pending": self.pending, "votes": self.votes, "revote": self.revote, "winner": self.winner,
            "night_done": list(self.night_done), "day_speakers": self.day_speakers, "day_voted": sorted(self.day_voted),
            "classification_by_event": self.classification_by_event, "operation_results": self.operation_results,
            "pause_requested": self.pause_requested, "operation_start_event_id": self.operation_start_event_id,
            "rules_version": self.rules_version, "seat_controllers": self.seat_controllers,
            "seat_model_configs": self.seat_model_configs, "witch_save_available": self.witch_save_available,
            "witch_poison_available": self.witch_poison_available, "witch_self_save_used": self.witch_self_save_used,
            "inspection_history": sorted(self.inspection_history), "death_reasons": self.death_reasons,
            "hunter_triggered": self.hunter_triggered, "hunter_processed": self.hunter_processed,
            "pending_last_words": self.pending_last_words, "pending_action": self.pending_action,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GameState":
        return cls(
            value["game_id"], value.get("seed"), {int(k): v for k, v in value["roles"].items()},
            set(value.get("alive", value["roles"].keys())), value.get("phase", "night"), value.get("day", 0),
            value.get("status", "running"), value.get("event_id", 0), value.get("pending", {}),
            {int(k): v for k, v in value.get("votes", {}).items()}, value.get("revote", False), value.get("winner"),
            set(value.get("night_done", [])), value.get("day_speakers", []), set(value.get("day_voted", [])),
            {int(k): v for k, v in value.get("classification_by_event", {}).items()}, value.get("operation_results", {}),
            value.get("pause_requested", False), value.get("operation_start_event_id"), value.get("rules_version", LEGACY_RULE_VERSION),
            {int(k): v for k, v in value.get("seat_controllers", {}).items()}, {int(k): v for k, v in value.get("seat_model_configs", {}).items()},
            value.get("witch_save_available", True), value.get("witch_poison_available", True), value.get("witch_self_save_used", False),
            set(value.get("inspection_history", [])), value.get("death_reasons", {}), value.get("hunter_triggered", False),
            value.get("hunter_processed", False), value.get("pending_last_words"), value.get("pending_action"),
        )


def assign_roles(seed: int | None = None, role_config: dict[int, str] | None = None, player_count: int = 10) -> dict[int, str]:
    if role_config:
        roles = {int(k): v for k, v in role_config.items()}
        if set(roles) == set(range(1, 7)) and sorted(roles.values()).count("wolf") == 2 and sorted(roles.values()).count("seer") == 1 and sorted(roles.values()).count("villager") == 3:
            return roles
        if set(roles) != set(TEN_PLAYERS) or any(sorted(roles.values()).count(k) != v for k, v in TEN_ROLE_COUNTS.items()):
            raise ValueError("role_config must contain players 1..10 with 3 wolf, 1 seer, 1 witch, 1 hunter, 4 villager")
        return roles
    if player_count == 6:
        rng = random.Random(seed); shuffled = list(range(1, 7)); rng.shuffle(shuffled)
        return {p: ("wolf" if p in shuffled[:2] else "seer" if p == shuffled[2] else "villager") for p in range(1, 7)}
    rng = random.Random(seed); shuffled = list(TEN_PLAYERS); rng.shuffle(shuffled); roles: dict[int, str] = {}; index = 0
    for role, count in TEN_ROLE_COUNTS.items():
        for _ in range(count): roles[shuffled[index]] = role; index += 1
    return roles


def check_target(state: GameState, actor: int, target: Any, *, allow_self: bool = False) -> str | None:
    if type(target) is not int: return "target must be an integer"
    if target not in state.roles or target not in state.alive: return f"target must be alive player 1..{max(state.roles)}"
    if not allow_self and target == actor: return "self target is not allowed"
    return None


def check_action(state: GameState, actor: int, action: dict[str, Any]) -> str | None:
    if actor not in state.alive and not (state.phase == "hunter_action" and state.roles.get(actor) == "hunter") and not (state.phase == "last_words" and state.pending_last_words == actor):
        return "dead player cannot act"
    if not isinstance(action, dict): return "action must be an object"
    kind = action.get("action")
    allowed = {"night": {"kill", "inspect", "save", "poison", "pass"}, "day_speak": {"speak", "last_words"}, "day_vote": {"vote", "pass"}, "hunter_action": {"shoot", "pass"}, "last_words": {"last_words", "pass"}}.get(state.phase, set())
    if kind not in allowed: return f"action {kind!r} is not allowed in phase {state.phase}"
    text, reason = action.get("text", ""), action.get("reason", "")
    if not isinstance(text, str) or len(text) > 500 or not isinstance(reason, str) or len(reason) > 300: return "text/reason must be strings within length limits"
    target = action.get("target")
    if state.phase == "hunter_action":
        if state.roles.get(actor) != "hunter": return "only hunter can shoot"
        if kind == "pass": return None
        return check_target(state, actor, target)
    if kind == "kill":
        if state.roles.get(actor) != "wolf": return "only wolves can kill"
        err = check_target(state, actor, target); return err or ("wolf cannot kill wolf teammate" if state.roles.get(target) == "wolf" else None)
    if kind == "inspect":
        if state.roles.get(actor) != "seer": return "only seer can inspect"
        if target in state.inspection_history: return "seer cannot inspect the same player twice"
        return check_target(state, actor, target)
    if kind == "save":
        if state.roles.get(actor) != "witch" or not state.witch_save_available or state.pending.get("witch_used"): return "save potion unavailable"
        if target is not None: return "save target must be null"
        if state.pending.get("night_kill_target") == actor and state.day > 0: return "witch cannot self-save after first night"
        return None
    if kind == "poison":
        if state.roles.get(actor) != "witch" or not state.witch_poison_available or state.pending.get("witch_used"): return "poison potion unavailable"
        return check_target(state, actor, target)
    if kind == "vote":
        if target is None: return None
        err = check_target(state, actor, target)
        if err: return err
        candidates = state.pending.get("revote_candidates") if state.revote else None
        return None if candidates is None or target in candidates else "revote target must be one of tied candidates"
    if kind in {"speak", "last_words", "pass"} and target is not None: return "target must be null"
    return None


def tally_votes(votes: dict[int, int | None]) -> tuple[int | None, dict[int, int]]:
    counts: dict[int, int] = {}
    for target in votes.values():
        if target is not None: counts[target] = counts.get(target, 0) + 1
    if not counts: return None, counts
    high = max(counts.values()); leaders = [p for p, n in counts.items() if n == high]
    return (leaders[0] if len(leaders) == 1 else None), counts


def winner(state: GameState) -> str | None:
    wolves = sum(state.roles[p] == "wolf" for p in state.alive)
    if len(state.roles) <= 6 and all(r in {"wolf", "seer", "villager"} for r in state.roles.values()):
        nonwolves = len(state.alive) - wolves
        if wolves == 0: return "villagers"
        if wolves >= nonwolves: return "wolves"
        return None
    gods = sum(state.roles[p] in {"seer", "witch", "hunter"} for p in state.alive)
    villagers = sum(state.roles[p] == "villager" for p in state.alive)
    if wolves == 0: return "draw" if gods == 0 or villagers == 0 else "villagers"
    if gods == 0 and villagers == 0: return "draw"
    if gods == 0 or villagers == 0: return "wolves"
    return None
