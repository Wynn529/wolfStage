from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

RULE_VERSION = "rules-v1.0"
PLAYERS = tuple(range(1, 7))


@dataclass
class GameState:
    """可序列化的六人局状态，包含阶段、存活者、票数和待处理夜间行动。"""
    game_id: str
    seed: int | None
    roles: dict[int, str]
    alive: set[int] = field(default_factory=lambda: set(PLAYERS))
    phase: str = "night"
    day: int = 0
    status: str = "running"
    event_id: int = 0
    pending: dict[str, Any] = field(default_factory=dict)
    votes: dict[int, int] = field(default_factory=dict)
    revote: bool = False
    winner: str | None = None
    night_done: set[str] = field(default_factory=set)
    day_speakers: list[int] = field(default_factory=list)
    day_voted: set[int] = field(default_factory=set)
    classification_by_event: dict[int, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """把集合和整数键转换成 JSON 可保存的状态快照。"""
        return {"game_id": self.game_id, "seed": self.seed, "roles": self.roles, "alive": sorted(self.alive), "phase": self.phase, "day": self.day, "status": self.status, "event_id": self.event_id, "pending": self.pending, "votes": self.votes, "revote": self.revote, "winner": self.winner, "night_done": list(self.night_done), "day_speakers": self.day_speakers, "day_voted": list(self.day_voted), "classification_by_event": self.classification_by_event}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GameState":
        """从 state.json 快照恢复 GameState，复原集合和整数键。"""
        return cls(value["game_id"], value.get("seed"), {int(k): v for k, v in value["roles"].items()}, set(value["alive"]), value["phase"], value["day"], value["status"], value["event_id"], value.get("pending", {}), {int(k): int(v) for k, v in value.get("votes", {}).items()}, value.get("revote", False), value.get("winner"), set(value.get("night_done", [])), value.get("day_speakers", []), set(value.get("day_voted", [])), {int(k): v for k, v in value.get("classification_by_event", {}).items()})


def assign_roles(seed: int | None = None, role_config: dict[int, str] | None = None) -> dict[int, str]:
    """按固定角色数量生成可复现身份，或校验调用方提供的角色配置。"""
    if role_config:
        roles = {int(k): v for k, v in role_config.items()}
        if sorted(roles) != list(PLAYERS) or sorted(roles.values()).count("wolf") != 2 or roles.get(next((p for p in PLAYERS if roles[p] == "seer"), -1)) != "seer":
            raise ValueError("role_config must contain players 1..6 with 2 wolf, 1 seer, 3 villager")
        if sorted(roles.values()).count("villager") != 3:
            raise ValueError("role_config must contain three villagers")
        return roles
    rng = random.Random(seed)
    shuffled = list(PLAYERS)
    rng.shuffle(shuffled)
    return {p: ("wolf" if p in shuffled[:2] else "seer" if p == shuffled[2] else "villager") for p in PLAYERS}


def check_target(state: GameState, actor: int, target: Any, *, allow_self: bool = False) -> str | None:
    """检查行动目标是否为合法存活玩家，合法时返回 None。"""
    if not isinstance(target, int):
        return "target must be an integer"
    if target not in PLAYERS or target not in state.alive:
        return "target must be alive player 1..6"
    if not allow_self and target == actor:
        return "self target is not allowed"
    return None


def check_action(state: GameState, actor: int, action: dict[str, Any]) -> str | None:
    """按当前阶段、角色、目标类型和文本长度校验代理行动。"""
    if actor not in state.alive:
        return "dead player cannot act"
    if not isinstance(action, dict):
        return "action must be an object"
    kind = action.get("action")
    allowed = {"night": {"kill", "inspect"}, "day_speak": {"speak"}, "day_vote": {"vote"}}.get(state.phase, set())
    if kind not in allowed:
        return f"action {kind!r} is not allowed in phase {state.phase}"
    text = action.get("text", "")
    reason = action.get("reason", "")
    if not isinstance(text, str) or len(text) > 500 or not isinstance(reason, str) or len(reason) > 300:
        return "text/reason must be strings within length limits"
    if kind == "kill":
        if state.roles.get(actor) != "wolf":
            return "only wolves can kill"
        return check_target(state, actor, action.get("target"))
    if kind == "inspect":
        if state.roles.get(actor) != "seer":
            return "only seer can inspect"
        return check_target(state, actor, action.get("target"))
    if kind == "vote":
        return check_target(state, actor, action.get("target"))
    if kind == "speak" and action.get("target") is not None:
        return "speak target must be null"
    return None


def winner(state: GameState) -> str | None:
    """根据存活身份判断 villagers、wolves 或尚未结束的 None。"""
    wolves = sum(state.roles[p] == "wolf" for p in state.alive)
    nonwolves = len(state.alive) - wolves
    if wolves == 0:
        return "villagers"
    if wolves >= nonwolves:
        return "wolves"
    return None


def tally_votes(votes: dict[int, int]) -> tuple[int | None, dict[int, int]]:
    """统计有效票并返回唯一最高票目标；平票或空票返回 None。"""
    counts: dict[int, int] = {}
    for target in votes.values():
        counts[target] = counts.get(target, 0) + 1
    if not counts:
        return None, counts
    high = max(counts.values())
    leaders = [p for p, n in counts.items() if n == high]
    return (leaders[0] if len(leaders) == 1 else None), counts
