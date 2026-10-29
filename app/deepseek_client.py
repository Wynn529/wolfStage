from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class ModelOutputError(ValueError):
    """请求已返回，但内容不可用；保留本次已知用量供有限纠错。"""
    def __init__(self, message: str, metadata: dict[str, Any]):
        super().__init__(message)
        self.metadata = metadata


class DeepSeekClient:
    """DeepSeek Chat Completions 的最小标准库适配器。"""
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
        self.timeout = timeout
        self.max_tokens = int(os.getenv("DEEPSEEK_MAX_TOKENS", "1024"))
        if not 1 <= self.max_tokens <= 4096:
            raise ValueError("DEEPSEEK_MAX_TOKENS must be between 1 and 4096")

    def complete(self, messages: list[dict[str, str]], *, agent_id: str, prompt_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.2,
                           "response_format": {"type": "json_object"}, "max_tokens": self.max_tokens,
                           "thinking": {"type": "disabled"}}).encode()
        req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.perf_counter()
        meta = {"agent_id":agent_id, "model_name":self.model, "model_version":self.model,
                "prompt_version":prompt_version, "input_tokens":None, "output_tokens":None}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            meta["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            exc.metadata = meta
            raise
        meta["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        try:
            payload = json.loads(raw)
            usage = payload.get("usage") or {}
            meta.update(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                        response_id=payload.get("id"), returned_model=payload.get("model"))
            choice = payload["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("model output was truncated")
            return json.loads(choice["message"]["content"]), meta
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise ModelOutputError("Invalid model JSON output: " + str(exc), meta) from exc
