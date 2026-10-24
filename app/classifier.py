from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


class AHttpClassifier:
    """Adapter for A's model.server_v031 /classify endpoint."""

    model_name = "small-intent-v0.1"

    def __init__(self, url: str | None = None, timeout: float = 5):
        """配置 A 服务地址和 HTTP 超时。"""
        self.url = (url or os.getenv("A_CLASSIFIER_URL", "http://127.0.0.1:8765/classify")).rstrip("/")
        self.timeout = timeout

    def __call__(self, game_id: str, event_id: int, speaker_id: int, public_text: str) -> dict[str, Any]:
        """向 A 发送一条公开发言并返回分类器 JSON。"""
        # A v0.3.1 要求 event_id 为非空字符串；B 内部事件编号仍保持整数。
        body = json.dumps({"game_id": game_id, "event_id": str(event_id), "speaker_id": speaker_id, "public_text": public_text}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))
