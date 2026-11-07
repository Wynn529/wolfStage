"""本机模型配置检查；--live 时仅对指定型号发起一次真实 JSON 行动测试。"""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.seat_config import MatchConfig
from app.seat_router import SeatRouter, ModelAttemptError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/nine_models.example.json")
    parser.add_argument("--profile", help="profile_id, e.g. mimo; required with --live")
    parser.add_argument("--live", action="store_true", help="send one real, potentially paid API request")
    parser.add_argument("--prompt-key", action="store_true", help="hidden key input; process memory only")
    args = parser.parse_args()
    config = MatchConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
    profiles = {p.profile_id: p for p in config.profiles}
    if not args.live:
        for p in config.profiles:
            print(json.dumps(dict(profile=p.profile_id, model=p.model, protocol=p.api_protocol,
                                  key_env=p.api_key_env, key_present=bool(os.environ.get(p.api_key_env))), ensure_ascii=False))
        return 0
    if args.profile not in profiles:
        parser.error("--live requires an existing --profile")
    profile = profiles[args.profile]
    seat = next((s for s in config.seats if s.control_type == "ai" and s.model_profile_id == args.profile), None)
    if seat is None:
        parser.error("profile is not assigned to an AI seat")
    if args.prompt_key:
        if not sys.stdin.isatty():
            parser.error("hidden key input requires an interactive terminal")
        key = getpass.getpass(f"{profile.api_key_env} (hidden, not saved): ")
        if not key:
            parser.error("empty key")
        os.environ[profile.api_key_env] = key
    if not os.environ.get(profile.api_key_env):
        parser.error("key missing; run in a terminal with --prompt-key")
    contract = dict(allowed_actions=["vote"], allowed_targets=[2],
                    required_fields=["action", "target", "text", "reason"])
    record = dict(profile=args.profile, model=profile.model,
                  timestamp=datetime.now(timezone.utc).isoformat(), scope="synthetic_json_vote_only")
    code = 1
    try:
        result = SeatRouter(config).act(seat.seat_id, context=dict(agent_id=seat.seat_id, events=[]),
                                         contract=contract, prompt="这是接口功能测试。请投2号，text为空，reason为接口测试。",
                                         prompt_version="access-smoke-v1")
        action = result.action
        valid = (action.get("action") == "vote" and type(action.get("target")) is int and action["target"] == 2
                 and isinstance(action.get("text"), str) and isinstance(action.get("reason"), str))
        record.update(meta=result.meta, action_fields_valid=valid)
        code = 0 if valid else 1
    except ModelAttemptError as exc:
        record.update(meta=exc.meta, action_fields_valid=False)
    finally:
        if args.prompt_key:
            os.environ.pop(profile.api_key_env, None)
    # 只写元数据与字段验证结果，不保存完整请求、正文、思考内容或凭据。
    directory = ROOT / ".local/model_checks"
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + profile.provider + ".json")
    filename.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=2))
    print("Evidence:", filename)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
