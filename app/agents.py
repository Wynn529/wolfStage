from __future__ import annotations

import json
import hashlib
import random
import threading
from pathlib import Path
from typing import Any, Callable

from .rules import GameState, check_action
from .deepseek_client import DeepSeekClient


ACTION_PROTOCOL_VERSION = "action-v1"


def action_contract(state: GameState, agent_id: int) -> dict[str, Any]:
    """只按当前阶段和本人已知信息给出行动约束，不向玩家泄露秘密。"""
    if len(state.roles) == 10:
        choices = legal_actions_for(state, agent_id)
        return {"version": ACTION_PROTOCOL_VERSION, "allowed_actions": [c["action"] for c in choices],
                "allowed_targets": list(dict.fromkeys(t for c in choices for t in c["targets"])),
                "actions": choices, "required_fields": ["action", "target", "text", "reason"],
                "text_max_length": 500, "reason_max_length": 300}
    role = state.roles[agent_id]
    if state.phase == "night":
        kind = {"wolf": "kill", "seer": "inspect"}.get(role)
    else:
        kind = {"day_speak": "speak", "day_vote": "vote"}.get(state.phase)
    targets = [p for p in sorted(state.alive) if p != agent_id]
    if kind == "kill":
        targets = [p for p in targets if state.roles[p] != "wolf"]
    return {
        "version": ACTION_PROTOCOL_VERSION,
        "allowed_actions": [kind] if kind else [],
        "allowed_targets": [None] if kind == "speak" else targets,
        "required_fields": ["action", "target", "text", "reason"],
        "text_max_length": 500,
        "reason_max_length": 300,
    }


def visible_events(events: list[dict[str, Any]], viewer: int | str) -> list[dict[str, Any]]:
    """按事件 visibility 过滤出指定玩家、主持或上帝视角可见的事件。"""
    out = []
    for event in events:
        visibility = event.get("visibility", "public")
        if visibility == "public" or viewer == "host" or viewer == "god":
            out.append(event)
        elif isinstance(visibility, list) and viewer in visibility:
            out.append(event)
    return out


def build_context(state: GameState, events: list[dict[str, Any]], agent_id: int, viewer_role: str) -> dict[str, Any]:
    """为角色代理组装只包含其可见事件、阶段和存活玩家的上下文。"""
    visible = visible_events(events, agent_id)
    # 只把最近记录和截短后的公开文本送入模型，避免整局长发言不断扩大请求延迟。
    model_events = []
    for event in visible[-24:]:
        compact = dict(event)
        if isinstance(compact.get("text"), str):
            compact["text"] = compact["text"][:400]
        if isinstance(compact.get("reason"), str):
            compact["reason"] = compact["reason"][:200]
        model_events.append(compact)
    context: dict[str, Any] = {
        "agent_id": agent_id,
        "role": viewer_role,
        "alive_players": sorted(state.alive),
        "phase": state.phase,
        "day": state.day,
        "events": model_events,
    }
    if viewer_role == "wolf":
        teammates = [p for p, role in sorted(state.roles.items()) if role == "wolf" and p != agent_id]
        context["wolf_teammates"] = teammates
        context["wolf_teammate_info"] = [
            {"player_id": p, "role": "wolf", "alive": p in state.alive} for p in teammates
        ]
        context["wolf_kill_proposals"] = dict(state.pending.get("kill_proposals", {}))
    if viewer_role == "seer":
        context["inspection_results"] = {
            int(event["target"]): event.get("result")
            for event in visible if event.get("type") == "inspection"
            and isinstance(event.get("target"), int)
        }
    if viewer_role == "witch":
        if state.witch_save_available:
            context["night_kill_target"] = state.pending.get("night_kill_target")
        context["witch_save_available"] = state.witch_save_available
        context["witch_poison_available"] = state.witch_poison_available
        context["witch_self_save_used"] = state.witch_self_save_used
    if viewer_role == "hunter":
        context["hunter_triggered"] = state.hunter_triggered
        context["hunter_processed"] = state.hunter_processed
        context["hunter_cause"] = state.death_reasons.get(agent_id)
    context["seat_controller"] = state.seat_controllers.get(agent_id, "ai")
    context["model_config"] = state.seat_model_configs.get(agent_id, {})
    context["legal_actions"] = legal_actions_for(state, agent_id)
    return context


def legal_actions_for(state: GameState, agent_id: int) -> list[dict[str, Any]]:
    """Return engine-provided action hints; clients must not infer skill eligibility."""
    role = state.roles.get(agent_id)
    kinds = {"night": {"wolf": ["kill"], "seer": ["inspect", "pass"],
                       "witch": ["save", "poison", "pass"]}.get(role, ["pass"]),
             "day_speak": ["speak"], "day_vote": ["vote", "pass"],
             "hunter_action": ["shoot", "pass"], "last_words": ["last_words", "pass"]}.get(state.phase, [])
    result = []
    for kind in kinds:
        if kind in {"kill", "inspect", "poison", "vote", "shoot"}:
            targets = [p for p in sorted(state.alive)
                       if check_action(state, agent_id, {"action": kind, "target": p}) is None]
            if kind == "kill":
                random.Random((state.seed or 0) * 100 + state.day * 10 + agent_id).shuffle(targets)
            if targets: result.append({"action": kind, "targets": targets})
        elif check_action(state, agent_id, {"action": kind, "target": None, "text": "发言"}) is None:
            result.append({"action": kind, "targets": []})
    return result


class SeatAgentRouter:
    """Routes each AI seat to an independently configured agent implementation."""
    model_name = "seat-router"
    model_version = "seat-router-v1"

    def __init__(self, default_agent: Any, agents: dict[str, Any] | None = None):
        self.default_agent = default_agent
        self.agents = agents or {}
        self.last_metadata: dict[str, Any] = {}

    def act(self, state: GameState, agent_id: int, context: dict[str, Any]) -> dict[str, Any]:
        config = state.seat_model_configs.get(agent_id, {})
        key = config.get("agent_key") or config.get("model") or config.get("model_name")
        agent = self.agents.get(key, self.default_agent)
        action = agent.act(state, agent_id, context)
        self.last_metadata = dict(getattr(agent, "last_metadata", {}) or {})
        self.model_name = config.get("model_name") or getattr(agent, "model_name", "unknown")
        self.model_version = config.get("model_version") or getattr(agent, "model_version", "unknown")
        return action


class StubAgent:
    """离线开发代理，生成合法且可重复的结构化行动。"""
    model_name = "stub"
    model_version = "stub-v1"

    def __init__(self, classifier: Callable[..., dict[str, Any]] | None = None):
        """初始化 stub；classifier 参数保留给本地联调注入。"""
        self.classifier = classifier

    def act(self, state: GameState, agent_id: int, context: dict[str, Any]) -> dict[str, Any]:
        """根据当前阶段返回 kill、inspect、speak 或 vote 行动 JSON。"""
        role = state.roles[agent_id]
        alive_others = [p for p in sorted(state.alive) if p != agent_id]
        if state.phase == "last_words":
            return {"action": "last_words", "target": None, "text": "我的遗言仅基于已知公开信息。", "reason": "stub last words"}
        if state.phase == "day_speak":
            return {"action": "speak", "target": None, "text": f"我是{agent_id}号，基于公开信息发言。", "reason": "stub decision"}
        if state.phase == "day_vote":
            target = alive_others[0] if alive_others else agent_id
            return {"action": "vote", "target": target, "text": f"我投 {target} 号", "reason": "stub vote"}
        if role == "wolf":
            nonwolves = [p for p in alive_others if state.roles[p] != "wolf"]
            return {"action": "kill", "target": nonwolves[0], "text": "夜间行动", "reason": "stub kill"}
        if role == "seer":
            targets = [p for p in alive_others if p not in state.inspection_history]
            return {"action": "inspect" if targets else "pass", "target": targets[0] if targets else None, "text": "查验", "reason": "stub inspect"}
        if role == "witch":
            return {"action": "pass", "target": None, "text": "", "reason": "stub pass"}
        if role == "hunter":
            return {"action": "pass", "target": None, "text": "", "reason": "stub pass"}
        return {"action": "inspect", "target": alive_others[0], "text": "", "reason": "unused"}


class DeepSeekAgent:
    """使用版本化角色 Prompt 调用 DeepSeek 的角色代理。"""
    model_name = "deepseek"
    action_protocol_version = ACTION_PROTOCOL_VERSION

    def __init__(self, client: DeepSeekClient | None = None, prompt_dir: str | Path = "prompts"):
        """配置 DeepSeek 客户端和 Prompt 文件目录。"""
        self.client = client or DeepSeekClient()
        self.model_version = self.client.model
        self.prompt_dir = prompt_dir
        self._metadata = threading.local()

    @property
    def last_metadata(self) -> dict[str, Any]:
        return getattr(self._metadata, 'value', {})

    @last_metadata.setter
    def last_metadata(self, value: dict[str, Any]) -> None:
        self._metadata.value = value

    def act(self, state: GameState, agent_id: int, context: dict[str, Any]) -> dict[str, Any]:
        """把角色上下文发送给 DeepSeek，并解析其 JSON 行动。"""
        role = state.roles[agent_id]
        prompt = load_prompt(role, self.prompt_dir)
        protocol = Path(__file__).resolve().parents[1] / "prompts" / "action-v1.md"
        prompt += "\n\n" + protocol.read_text(encoding="utf-8")
        request_context = dict(context, action_contract=action_contract(state, agent_id))
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(request_context, ensure_ascii=False)}]
        self.last_metadata = {}
        prompt_hash = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
        try:
            action, metadata = self.client.complete(messages, agent_id=str(agent_id), prompt_version=f"{role}-v1+{ACTION_PROTOCOL_VERSION}")
        except Exception as exc:
            self.last_metadata = dict(getattr(exc, 'metadata', {}) or {}, prompt_hash=prompt_hash)
            raise
        self.last_metadata = dict(metadata, prompt_hash=prompt_hash)
        return action


def load_prompt(role: str, prompt_dir: str | Path = "prompts") -> str:
    """读取指定角色的版本化 Prompt；文件缺失时返回安全的 JSON 约束兜底文本。"""
    filename = {"wolf": "wolf-v1.md", "seer": "seer-v1.md", "villager": "villager-v1.md", "witch": "witch-v1.md", "hunter": "hunter-v1.md", "host": "host-v1.md"}[role]
    path = Path(prompt_dir) / filename
    return path.read_text(encoding="utf-8") if path.exists() else "Return valid JSON only."
