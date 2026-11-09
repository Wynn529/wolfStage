"""基于官方调用示例的无状态供应商适配。一次 complete 仅发送一次请求。"""
import json
import os
import time
import urllib.error
import urllib.request

from .seat_config import ModelProfile


PROVIDERS = {"mimo", "tokenhub", "minimax", "kimi", "ark", "qianfan", "zhipu", "deepseek", "bailian"}

# 仅记录官方已知枚举；不输出可能含凭据或账号信息的原始错误正文。
SAFE_ERROR_CODES = {
    "ModelNotOpen", "PathNotFound", "InvalidEndpointOrModel.NotFound",
    "InvalidEndpointOrModel.ModelIDAccessDisabled", "NotFound",
    "AuthenticationError", "Unauthorized", "invalid_api_key",
    "PermissionDenied", "RateLimitExceeded", "InsufficientQuota",
    "1000", "1001", "1003", "1005",
}


def safe_http_error_code(exc):
    try:
        payload = json.loads(exc.read(65536).decode("utf-8"))
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if type(code) is int:
            code = str(code)
        return code if code in SAFE_ERROR_CODES else None
    except (ValueError, UnicodeError, AttributeError, TypeError, OSError):
        return None


class ProviderError(RuntimeError):
    def __init__(self, meta):
        super().__init__("provider request failed; inspect safe metadata")
        self.meta = meta


def is_request_timeout(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 504}
    return isinstance(exc, TimeoutError) or (
        isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)
    )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 防止鉴权头被转发至其他主机。
        return None


class ProviderClient:
    def __init__(self, profile: ModelProfile):
        if profile.provider not in PROVIDERS:
            raise ValueError("unsupported provider: " + profile.provider)
        self.profile = profile
        self.model = profile.model

    def _send(self, request):
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=self.profile.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def complete(self, messages, *, agent_id, prompt_version):
        p = self.profile
        started = time.perf_counter()
        meta = dict(agent_id=agent_id, prompt_version=prompt_version, model_name=None,
                    model_version=None, input_tokens=None, output_tokens=None, latency_ms=None)
        try:
            key = os.environ.get(p.api_key_env)
            if not key:
                raise ValueError("missing_api_key")
            options = json.loads(p.request_options_json)
            body = dict(model=p.model, stream=False, **options)
            if p.api_protocol == "responses":
                body["input"] = messages
                body["store"] = False
                path = "/responses"
            else:
                body["messages"] = messages
                path = "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if p.provider == "mimo":
                headers["api-key"] = key
            else:
                headers["Authorization"] = "Bearer " + key
            request = urllib.request.Request(p.base_url.rstrip("/") + path,
                                             data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                             headers=headers, method="POST")
            payload = self._send(request)
            actual = payload.get("model")
            meta.update(model_name=actual, model_version=actual,
                        response_id=payload.get("id"))
            usage = payload.get("usage") or {}
            if p.api_protocol == "responses":
                meta.update(input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"))
                if payload.get("status") != "completed" or payload.get("error"):
                    raise ValueError("response_not_completed")
                content = "".join(part.get("text", "") for item in payload.get("output", [])
                                  if item.get("type") == "message" and item.get("role") == "assistant"
                                  for part in item.get("content", []) if part.get("type") == "output_text")
            else:
                meta.update(input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))
                choice = payload["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise ValueError("response_not_completed")
                content = choice["message"].get("content")
            action = json.loads(content)
            if not isinstance(action, dict):
                raise ValueError("action_not_object")
        except Exception as exc:
            meta.update(latency_ms=round((time.perf_counter() - started) * 1000, 2),
                        success=False, error_type=type(exc).__name__)
            if is_request_timeout(exc):
                meta["request_timeout"] = True
            if isinstance(exc, urllib.error.HTTPError):
                meta["http_status"] = exc.code
                code = safe_http_error_code(exc)
                if code is not None:
                    meta["provider_error_code"] = code
                exc.close()
            raise ProviderError(meta) from None
        meta.update(latency_ms=round((time.perf_counter() - started) * 1000, 2), success=True)
        return action, meta
