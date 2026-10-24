from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EventStore:
    """以 JSONL 和 JSON 文件保存事件、调用、错误、状态及汇总。"""
    def __init__(self, root: str | Path = "runs"):
        """设置所有对局证据文件的根目录。"""
        self.root = Path(root)

    def game_dir(self, game_id: str) -> Path:
        """创建并返回指定对局的证据目录。"""
        path = self.root / game_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def append(self, game_id: str, event: dict[str, Any]) -> None:
        """追加一条逐事件原始记录。"""
        with (self.game_dir(game_id) / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def append_call(self, game_id: str, call: dict[str, Any]) -> None:
        """追加一次模型或分类器调用记录。"""
        with (self.game_dir(game_id) / "calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(call, ensure_ascii=False, sort_keys=True) + "\n")

    def append_error(self, game_id: str, error: dict[str, Any]) -> None:
        """追加一次失败或降级记录。"""
        with (self.game_dir(game_id) / "errors.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n")

    def read(self, game_id: str) -> list[dict[str, Any]]:
        """读取指定对局的全部事件。"""
        path = self.game_dir(game_id) / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def read_calls(self, game_id: str) -> list[dict[str, Any]]:
        """读取指定对局的全部调用记录。"""
        path = self.game_dir(game_id) / "calls.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def write_summary(self, game_id: str, summary: dict[str, Any]) -> None:
        """写入由原始日志计算出的场次汇总。"""
        (self.game_dir(game_id) / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    def write_state(self, game_id: str, state: dict[str, Any]) -> None:
        """保存可用于服务重启恢复的游戏状态快照。"""
        (self.game_dir(game_id) / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def read_state(self, game_id: str) -> dict[str, Any] | None:
        """读取状态快照；不存在时返回 None。"""
        path = self.game_dir(game_id) / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def list_games(self) -> list[str]:
        """列出具有状态快照、可在重启时恢复的对局。"""
        if not self.root.exists():
            return []
        return [p.name for p in self.root.iterdir() if p.is_dir() and (p / "state.json").exists()]

    def replay(self, game_id: str) -> list[dict[str, Any]]:
        """只读返回事件日志，绝不触发模型调用。"""
        return self.read(game_id)
