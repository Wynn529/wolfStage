"""C 首轮受限真实请求；不修改 B 客户端或原始调用日志。"""
from __future__ import annotations
import json
from decimal import Decimal
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
from app.deepseek_client import DeepSeekClient, ModelOutputError


class SmokeClient(DeepSeekClient):
    max_attempts = 12
    reservation_cny = Decimal("0.04")
    maximum_reserved_cny = Decimal("0.98")
    max_input_bytes = 8000
    max_tokens = 512

    def __init__(self, ledger: Path, **kwargs):
        super().__init__(**kwargs)
        self.max_tokens = type(self).max_tokens  # 首轮512上限不受通用客户端默认值影响。
        if self.model != "deepseek-flash":
            raise ValueError("C smoke check requires deepseek-flash; restart with the updated launcher.")
        self.ledger = Path(ledger)
        self.lock = threading.Lock()

    def complete(self, messages, *, agent_id, prompt_version):
        if not self.api_key:
            raise RuntimeError("Local API key is missing.")
        message_bytes = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
        if message_bytes > self.max_input_bytes:
            raise RuntimeError("C smoke input limit reached; no request was sent.")
        with self.lock:
            value = json.loads(self.ledger.read_text(encoding="utf-8")) if self.ledger.exists() else {"attempts": 0, "reserved_cny": 0}
            count = value["attempts"]
            reserved = Decimal(str(value.get('reserved_cny', count * .15)))
            if not reserved.is_finite() or reserved < 0:
                raise RuntimeError("C smoke ledger is invalid; no request was sent.")
            next_reserved = reserved + self.reservation_cny
            if type(count) is not int or count < 0 or count >= self.max_attempts or next_reserved > self.maximum_reserved_cny:
                raise RuntimeError("C smoke request budget exhausted; no request was sent.")
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            value.update(attempts=count + 1, reserved_cny=float(next_reserved),
                         reservation_per_new_request_cny=float(self.reservation_cny),
                         pricing_checked="2026-10-30",
                         authorized_cny=1, model=self.model)
            temporary = self.ledger.with_suffix(".tmp")
            temporary.write_text(json.dumps(value), encoding="utf-8")
            temporary.replace(self.ledger)
            attempt = count + 1
        body = json.dumps({"model": self.model, "messages": messages, "temperature": .2,
                           "response_format": {"type": "json_object"}, "max_tokens": self.max_tokens,
                           "thinking": {"type": "disabled"}, "stream": False}).encode()
        request = Request("https://api.deepseek.com/chat/completions", data=body,
                          headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
        started = time.perf_counter()
        meta = {"attempt": attempt, "agent_id": agent_id, "model_name": self.model,
                "model_version": self.model, "prompt_version": prompt_version,
                "input_tokens": None, "output_tokens": None, "success": False}
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            usage = payload.get("usage", {})
            meta.update(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                        returned_model=payload.get("model"), response_id=payload.get("id"))
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelOutputError("C smoke output token limit reached; keep this failed attempt.", meta)
            try:
                action = json.loads(choice["message"]["content"])
            except (ValueError, KeyError, TypeError) as exc:
                raise ModelOutputError("Invalid model JSON output", meta) from exc
            meta["success"] = True
            return action, meta
        finally:
            meta["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            with self.lock:
                with self.ledger.with_name("smoke_usage_20261029.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(meta) + "\n")
