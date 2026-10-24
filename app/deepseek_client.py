from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any


class DeepSeekClient:
    """DeepSeek Chat Completions 的最小标准库适配器。"""
    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: float = 30):
        """从参数或环境变量读取密钥、模型名和请求超时。"""
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]], *, agent_id: str, prompt_version: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """发送 JSON 模式请求，返回解析后的行动和用量/延迟元数据。"""
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0.2, "response_format": {"type": "json_object"}}).encode()
        req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.perf_counter()
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        action = json.loads(content)
        usage = payload.get("usage", {})
        meta = {"agent_id": agent_id, "model_name": self.model, "model_version": self.model, "prompt_version": prompt_version, "latency_ms": round((time.perf_counter() - started) * 1000, 2), "input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")}
        return action, meta
