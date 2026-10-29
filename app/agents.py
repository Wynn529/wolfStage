from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .rules import GameState
from .deepseek_client import DeepSeekClient


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
    context: dict[str, Any] = {
        "agent_id": agent_id,
        "role": viewer_role,
        "alive_players": sorted(state.alive),
        "phase": state.phase,
        "day": state.day,
        "events": visible,
    }
    if viewer_role == "wolf":
        context["wolf_teammates"] = [p for p, role in state.roles.items() if role == "wolf" and p != agent_id]
    return context


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
        if state.phase == "day_speak":
            return {"action": "speak", "target": None, "text": f"我是{agent_id}号，基于公开信息发言。", "reason": "stub decision"}
        if state.phase == "day_vote":
            target = alive_others[0] if alive_others else agent_id
            return {"action": "vote", "target": target, "text": f"我投 {target} 号", "reason": "stub vote"}
        if role == "wolf":
            nonwolves = [p for p in alive_others if state.roles[p] != "wolf"]
            return {"action": "kill", "target": nonwolves[0], "text": "夜间行动", "reason": "stub kill"}
        if role == "seer":
            return {"action": "inspect", "target": alive_others[0], "text": "查验", "reason": "stub inspect"}
        return {"action": "inspect", "target": alive_others[0], "text": "", "reason": "unused"}


class DeepSeekAgent:
    """使用版本化角色 Prompt 调用 DeepSeek 的角色代理。"""
    model_name = "deepseek"

    def __init__(self, client: DeepSeekClient | None = None, prompt_dir: str | Path = "prompts"):
        """配置 DeepSeek 客户端和 Prompt 文件目录。"""
        self.client = client or DeepSeekClient()
        self.model_version = self.client.model
        self.prompt_dir = prompt_dir
        self.last_metadata: dict[str, Any] = {}

    def act(self, state: GameState, agent_id: int, context: dict[str, Any]) -> dict[str, Any]:
        """把角色上下文发送给 DeepSeek，并解析其 JSON 行动。"""
        role = state.roles[agent_id]
        prompt = load_prompt(role, self.prompt_dir)
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
        action, metadata = self.client.complete(messages, agent_id=str(agent_id), prompt_version=f"{role}-v1")
        self.last_metadata = metadata
        return action


def load_prompt(role: str, prompt_dir: str | Path = "prompts") -> str:
    """读取指定角色的版本化 Prompt；文件缺失时返回安全的 JSON 约束兜底文本。"""
    filename = {"wolf": "wolf-v1.md", "seer": "seer-v1.md", "villager": "villager-v1.md", "host": "host-v1.md"}[role]
    path = Path(prompt_dir) / filename
    return path.read_text(encoding="utf-8") if path.exists() else "Return valid JSON only."
