from __future__ import annotations

import json
import os
from errno import EACCES
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Any

from .rules import GameState


def decode_jsonl(text: str) -> list[Any]:
    """Split JSONL on LF, keeping Unicode separators inside JSON strings."""
    return [json.loads(line) for line in text.split("\n") if line.strip()]


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

    def append_call(self, game_id: str, call: dict[str, Any], *, atomic: bool = False) -> None:
        """追加一次模型或分类器调用记录。"""
        path = self.game_dir(game_id) / "calls.jsonl"
        line = json.dumps(call, ensure_ascii=False, sort_keys=True) + "\n"
        if atomic:
            previous = path.read_bytes().decode("utf-8") if path.exists() else ""
            separator = "\n" if previous and not previous.endswith("\n") else ""
            self._replace(path, previous + separator + line)
        else:
            with path.open("a", encoding="utf-8") as f:
                f.write(line)

    def append_error(self, game_id: str, error: dict[str, Any]) -> None:
        """追加一次失败或降级记录。"""
        with (self.game_dir(game_id) / "errors.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(error, ensure_ascii=False, sort_keys=True) + "\n")

    def read(self, game_id: str) -> list[dict[str, Any]]:
        """读取指定对局的全部事件。"""
        path = self.game_dir(game_id) / "events.jsonl"
        return decode_jsonl(self._read_text(path) or "")

    def validate_saved_log(self, game_id: str) -> int:
        events = self.read(game_id)
        self._validate_event_sequence(game_id, events)
        return len(events)

    def _validate_event_sequence(self, game_id: str, events: list[dict[str, Any]]) -> None:
        self._validate_events(game_id, events)
        if any(event["event_id"] != index for index, event in enumerate(events, 1)):
            raise ValueError(f"Non-consecutive event log in game {game_id}")

    @staticmethod
    def _validate_events(game_id: str, events: list[dict[str, Any]]) -> None:
        if any(not isinstance(event, dict) or event.get("game_id") != game_id
               or type(event.get("event_id")) is not int or event["event_id"] < 1
               or not isinstance(event.get("type"), str) for event in events):
            raise ValueError(f"Invalid event log in game {game_id}")

    def read_calls(self, game_id: str) -> list[dict[str, Any]]:
        """读取指定对局的全部调用记录。"""
        path = self.game_dir(game_id) / "calls.jsonl"
        return decode_jsonl(self._read_text(path) or "")

    def write_summary(self, game_id: str, summary: dict[str, Any]) -> None:
        """写入由原始日志计算出的场次汇总。"""
        (self.game_dir(game_id) / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )

    def write_state(self, game_id: str, state: dict[str, Any]) -> None:
        """保存可用于服务重启恢复的游戏状态快照。"""
        self._replace(self.game_dir(game_id) / "state.json", json.dumps(state, ensure_ascii=False, indent=2))

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(content)
                stream.close()
                EventStore._retry_sharing(lambda: temporary.replace(path))
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def _retry_sharing(operation):
        # Windows file readers and atomic replacements can briefly block each other.
        for delay in (0.01, 0.02, 0.04, 0.08, 0.16, None):
            try:
                return operation()
            except PermissionError as exc:
                transient = (getattr(exc, "winerror", None) in {5, 32, 33}
                             or (os.name == "nt" and exc.errno == EACCES))
                if delay is None or not transient:
                    raise
                sleep(delay)

    def _read_text(self, path: Path) -> str | None:
        try:
            return self._retry_sharing(lambda: path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def commit_events(self, game_id: str, state: dict[str, Any],
                      events: list[dict[str, Any]], summary: dict[str, Any] | None = None) -> None:
        """Persist a state transition before publishing its events."""
        # Keep the existing journal filename so pending vote commits still recover.
        path = self.game_dir(game_id) / "vote_commit.json"
        self._replace(path, json.dumps({"state": state, "events": events, "summary": summary}, ensure_ascii=False))
        self.recover_events(game_id)

    def recover_events(self, game_id: str) -> dict[str, Any] | None:
        folder = self.game_dir(game_id)
        journal = folder / "vote_commit.json"
        if not journal.exists():
            return None
        commit = json.loads(journal.read_text(encoding="utf-8"))
        state, events, summary = commit["state"], commit["events"], commit["summary"]
        if (not isinstance(state, dict) or state.get("game_id") != game_id
                or not isinstance(events, list) or (summary is not None and not isinstance(summary, dict))):
            raise ValueError(f"Invalid event commit in game {game_id}")
        GameState.from_dict(state)
        self._validate_events(game_id, events)
        log = folder / "events.jsonl"
        try:
            original = self._retry_sharing(log.read_bytes).decode("utf-8")
        except FileNotFoundError:
            original = ""
        recorded = decode_jsonl(original)
        pending = events
        if events:
            start = events[0]["event_id"]
            tail = recorded[start - 1:]
            if tail != events[:len(tail)]:
                raise ValueError(f"Conflicting event commit in game {game_id}")
            pending = events[len(tail):]
        combined = recorded + pending
        self._validate_event_sequence(game_id, combined)
        cursor = state.get("event_id")
        if type(cursor) is not int or cursor != len(combined):
            raise ValueError(f"Invalid event commit cursor in game {game_id}")
        if pending:
            separator = "\n" if original and not original.endswith("\n") else ""
            suffix = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in pending)
            self._replace(log, original + separator + suffix)
        self.write_state(game_id, state)
        if summary is not None and not summary.get("restricted"):
            self.write_summary(game_id, summary)
        journal.unlink()
        return state

    def read_state(self, game_id: str) -> dict[str, Any] | None:
        """读取状态快照；不存在时返回 None。"""
        path = self.game_dir(game_id) / "state.json"
        content = self._read_text(path)
        return json.loads(content) if content is not None else None

    def read_classification_checkpoint(self, game_id: str) -> dict[str, Any] | None:
        content = self._read_text(self.game_dir(game_id) / "classification_checkpoint.json")
        return json.loads(content) if content is not None else None

    def write_classification_checkpoint(self, game_id: str, checkpoint: dict[str, Any]) -> None:
        path = self.game_dir(game_id) / "classification_checkpoint.json"
        self._replace(path, json.dumps(checkpoint, ensure_ascii=False))

    def list_games(self) -> list[str]:
        """列出具有状态快照、可在重启时恢复的对局。"""
        if not self.root.exists():
            return []
        return [p.name for p in self.root.iterdir() if p.is_dir() and (p / "state.json").exists()]

    def replay(self, game_id: str) -> list[dict[str, Any]]:
        """只读返回事件日志，绝不触发模型调用。"""
        return self.read(game_id)
