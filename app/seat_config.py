"""开局冻结的接入配置；身份分配和规则校验属于引擎。"""
from dataclasses import asdict, dataclass
import re
import json
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    display_name: str
    provider: str
    model: str
    api_key_env: str
    timeout: float = 30
    base_url: str = "https://api.deepseek.com"
    api_protocol: str = "chat_completions"
    request_options_json: str = "{}"

    def __post_init__(self):
        if not all((self.profile_id, self.display_name, self.provider, self.model)):
            raise ValueError("model profile fields must be nonempty")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
            raise ValueError("api_key_env must be an environment variable name")
        if not 0 < self.timeout <= 300:
            raise ValueError("timeout must be in (0, 300]")
        url = urlsplit(self.base_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("base_url must be an HTTPS endpoint without credentials/query")
        if self.api_protocol not in ("chat_completions", "responses"):
            raise ValueError("unsupported API protocol")
        options = json.loads(self.request_options_json)
        allowed = {"max_completion_tokens", "max_output_tokens", "thinking", "reasoning_effort", "temperature", "top_p"}
        if not isinstance(options, dict) or set(options) - allowed:
            raise ValueError("unsupported request options")


@dataclass(frozen=True)
class SeatConfig:
    seat_id: int
    control_type: str
    model_profile_id: str | None = None


@dataclass(frozen=True)
class MatchConfig:
    mode: str
    seats: tuple[SeatConfig, ...]
    profiles: tuple[ModelProfile, ...]
    human_role: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "seats", tuple(self.seats))
        object.__setattr__(self, "profiles", tuple(self.profiles))
        if len(self.seats) != 10 or {s.seat_id for s in self.seats} != set(range(1, 11)):
            raise ValueError("exactly ten unique seats numbered 1..10 required")
        ids = {p.profile_id for p in self.profiles}
        if len(ids) != len(self.profiles):
            raise ValueError("duplicate model profile id")
        humans = [s for s in self.seats if s.control_type == "human"]
        if self.mode not in ("all_ai", "human") or len(humans) != (self.mode == "human"):
            raise ValueError("mode requires zero or one human seat")
        if self.human_role not in (None, "wolf", "seer", "witch", "hunter", "villager"):
            raise ValueError("unknown human role")
        if self.mode == "all_ai" and self.human_role is not None:
            raise ValueError("human_role requires human mode")
        for seat in self.seats:
            if seat.control_type == "ai":
                if seat.model_profile_id not in ids:
                    raise ValueError("unknown model profile")
            elif seat.control_type != "human" or seat.model_profile_id is not None:
                raise ValueError("human seat must not have a model profile")

    def snapshot(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(mode=value["mode"], seats=tuple(SeatConfig(**s) for s in value["seats"]),
                   profiles=tuple(ModelProfile(**p) for p in value["profiles"]),
                   human_role=value.get("human_role"))
