"""启动九模型联调服务；密钥仅隐藏输入到服务进程环境，不落盘。"""
import argparse
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.seat_config import MatchConfig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="prompt keys and start service; advance may incur API fees")
    parser.add_argument("--port", type=int, default=18083)
    args = parser.parse_args()
    config = MatchConfig.from_dict(json.loads((ROOT / "config/nine_models.example.json").read_text(encoding="utf-8")))
    environment = os.environ.copy()
    if not args.serve:
        for p in config.profiles:
            print(p.profile_id, p.model, "key_present=" + str(bool(environment.get(p.api_key_env))))
        print("Use --serve in an interactive terminal to input keys and start the service.")
        return 0
    if not sys.stdin.isatty():
        parser.error("interactive terminal required for hidden key input")
    for p in config.profiles:
        if not environment.get(p.api_key_env):
            key = getpass.getpass(f"{p.display_name} / {p.api_key_env} (hidden): ").strip()
            if not key:
                parser.error("all configured model keys required; no request was sent")
            environment[p.api_key_env] = key
    print(f"Service: http://127.0.0.1:{args.port}/docs ; startup creates no paid model request.")
    return subprocess.call([sys.executable, "-m", "uvicorn", "app.integration_service:create_app", "--factory",
                            "--host", "127.0.0.1", "--port", str(args.port)], cwd=ROOT, env=environment)


if __name__ == "__main__":
    raise SystemExit(main())
