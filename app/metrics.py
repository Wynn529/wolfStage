from __future__ import annotations

from typing import Any

from .events import EventStore


def summarize(store: EventStore, game_id: str, *, staged_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """从事件和调用原始日志重算调用次数、token、重试、失败和胜方。"""
    events = store.read(game_id) + (staged_events or [])
    calls = store.read_calls(game_id)
    summary = {
        "game_id": game_id,
        "event_count": len(events),
        "call_count": len(calls),
        "successful_calls": sum(1 for c in calls if c.get("success")),
        "failed_calls": sum(1 for c in calls if not c.get("success")),
        "retries": sum(1 for c in calls if c.get("attempt", 1) > 1),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
        "winner": next((e.get("winner") for e in reversed(events) if e.get("type") == "game_over"), None),
        "rules_version": next((e.get("rules_version") for e in events if e.get("rules_version")), None),
    }
    by_seat: dict[str, dict[str, Any]] = {}
    for call in calls:
        key = str(call.get("agent_id", call.get("seat_id", "unknown")))
        row = by_seat.setdefault(key, {"seat_id": call.get("agent_id", call.get("seat_id")), "model_name": call.get("model_name"), "model_version": call.get("model_version"), "calls": 0, "success": 0, "failed": 0, "input_tokens_known": 0, "output_tokens_known": 0, "input_tokens_missing": 0, "output_tokens_missing": 0})
        row["calls"] += 1; row["success"] += int(bool(call.get("success"))); row["failed"] += int(not call.get("success"))
        for field in ("input_tokens", "output_tokens"):
            if type(call.get(field)) is int and call[field] >= 0: row[field + "_known"] += call[field]
            elif call.get("caller_type") != "small_model": row[field + "_missing"] += 1
    summary["by_seat"] = list(by_seat.values())
    for key in ("input_tokens", "output_tokens"):
        applicable = [c for c in calls if c.get("caller_type") != "small_model" and c.get("model_name") != "stub"]
        known = [c[key] for c in applicable if type(c.get(key)) is int and c[key] >= 0]
        missing = len(applicable) - len(known)
        summary[key + "_known"] = sum(known)
        summary[key + "_missing_calls"] = missing
        summary[key + "_not_applicable_calls"] = len(calls) - len(applicable)
        summary[key] = None if missing or (calls and not applicable) else sum(known)
    if staged_events is None:
        store.write_summary(game_id, summary)
    return summary
