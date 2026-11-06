"""无记忆的按席位模型路由；一次调用对应一次尝试，不自行重试或换模型。"""
from copy import deepcopy
from dataclasses import dataclass
import json
import time
from typing import Any

from .provider_client import ProviderClient, ProviderError
from .seat_config import MatchConfig, ModelProfile


@dataclass
class ActionResult:
    action: dict[str, Any]
    meta: dict[str, Any]


class ModelAttemptError(RuntimeError):
    def __init__(self, meta):
        super().__init__("model attempt failed; inspect safe metadata")
        self.meta = meta


def create_client(profile: ModelProfile):
    return ProviderClient(profile)


class SeatRouter:
    def __init__(self, config: MatchConfig, client_factory=create_client):
        self.config = config
        self._profiles = {p.profile_id: p for p in config.profiles}
        self._seats = {s.seat_id: s for s in config.seats}
        self._factory = client_factory

    def act(self, seat_id: int, *, context: dict, contract: dict,
            prompt: str, prompt_version: str) -> ActionResult:
        """context 必须已由引擎按席位过滤；contract/Prompt 由规则负责人提供。"""
        seat = self._seats[seat_id]
        if seat.control_type == "human":
            raise ValueError("waiting_human: do not call a player model")
        profile = self._profiles[seat.model_profile_id]
        if context.get("agent_id") != seat_id:
            raise ValueError("context seat mismatch")
        request = deepcopy(context)
        request["action_contract"] = deepcopy(contract)
        messages = [{"role": "system", "content": prompt + "\n只返回一个符合 action_contract 的完整 JSON 对象，包含 action、target、text、reason。不要 Markdown 或额外文字。"},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
        base = dict(provider=profile.provider, model_profile_id=profile.profile_id,
                    requested_model=profile.model, model_name=None, model_version=None,
                    agent_id=seat_id, prompt_version=prompt_version,
                    input_tokens=None, output_tokens=None, latency_ms=None, success=False)
        started = time.perf_counter()
        try:
            client = self._factory(profile)
            action, meta = client.complete(messages, agent_id=str(seat_id), prompt_version=prompt_version)
            if not isinstance(action, dict):
                raise ValueError("model action must be an object")
        except ProviderError as exc:
            raise ModelAttemptError(dict(base, **exc.meta)) from None
        except Exception as exc:
            # 不把供应商异常正文（可能含请求/凭据）送入网页或日志。
            raise ModelAttemptError(dict(base, error_type=type(exc).__name__,
                                         latency_ms=round((time.perf_counter() - started) * 1000, 2))) from None
        return ActionResult(action, dict(base, **{k: meta[k] for k in (
            "model_name", "model_version", "input_tokens", "output_tokens", "latency_ms", "response_id") if k in meta},
            success=True))


class EngineSeatAgent:
    """将新引擎冻结的每席配置桥接到已实测的九家路由；不回退默认模型。"""
    def __init__(self):
        self.last_metadata = {}

    def act(self, state, agent_id, context):
        from .agents import action_contract, load_prompt
        from .seat_config import SeatConfig
        from pathlib import Path
        profiles = {c["profile_id"]: ModelProfile(**c) for c in state.seat_model_configs.values() if c.get("profile_id")}
        seats = tuple(SeatConfig(p, state.seat_controllers[p],
                      state.seat_model_configs.get(p, {}).get("profile_id")) for p in sorted(state.roles))
        config = MatchConfig("human" if any(s.control_type == "human" for s in seats) else "all_ai", seats, tuple(profiles.values()))
        role = state.roles[agent_id]
        prompt_dir = Path(__file__).resolve().parents[1] / "prompts"
        prompt = load_prompt(role, prompt_dir) + "\n" + (prompt_dir / "action-v1.md").read_text(encoding="utf-8")
        self.last_metadata = {}
        try:
            result = SeatRouter(config).act(agent_id, context=context, contract=action_contract(state, agent_id),
                                            prompt=prompt, prompt_version=f"{role}-v1+action-v1")
        except ModelAttemptError as exc:
            self.last_metadata = exc.meta
            exc.metadata = exc.meta
            raise
        self.last_metadata = result.meta
        return result.action
