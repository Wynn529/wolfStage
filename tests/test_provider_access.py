import io
import json
from pathlib import Path
import urllib.error

import pytest

from app.provider_client import ProviderClient, ProviderError
from app.seat_config import MatchConfig, ModelProfile
from app.seat_router import SeatRouter, ModelAttemptError

ROOT = Path(__file__).resolve().parents[1]
CONFIG = MatchConfig.from_dict(json.loads((ROOT / "config/nine_models.example.json").read_text(encoding="utf-8")))
ACTION = {"action": "vote", "target": 2, "text": "", "reason": "测试"}


def response(protocol, text=None):
    text = json.dumps(ACTION) if text is None else text
    common = dict(model="actual-model-version", id="response-test")
    if protocol == "responses":
        return dict(common, status="completed", usage={"input_tokens": 31, "output_tokens": 7}, output=[
            {"type": "reasoning", "summary": [{"text": "not action"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}])
    return dict(common, usage={"prompt_tokens": 31, "completion_tokens": 7}, choices=[
        {"finish_reason": "stop", "message": {"content": text, "reasoning_content": "not action"}}])


@pytest.mark.parametrize("profile", CONFIG.profiles, ids=lambda p: p.profile_id)
def test_all_nine_request_shapes_and_routing(profile, monkeypatch):
    monkeypatch.setenv(profile.api_key_env, "test-key")
    requests = []
    def send(self, req):
        requests.append(req)
        assert req.full_url == profile.base_url + ("/responses" if profile.api_protocol == "responses" else "/chat/completions")
        body = json.loads(req.data)
        assert body["model"] == profile.model and body["stream"] is False
        expected = "input" if profile.api_protocol == "responses" else "messages"
        assert expected in body
        if profile.api_protocol == "responses":
            assert body["store"] is False and "previous_response_id" not in body
        assert json.loads(body[expected][1]["content"])["agent_id"] == seat.seat_id
        if profile.provider == "minimax":
            assert body["reasoning_effort"] == "low"
            assert "temperature" not in body and "thinking" not in body
        if profile.provider == "mimo":
            assert req.get_header("Api-key") == "test-key"
        else:
            assert req.get_header("Authorization") == "Bearer test-key"
        return response(profile.api_protocol)
    monkeypatch.setattr(ProviderClient, "_send", send)
    seat = next(s for s in CONFIG.seats if s.model_profile_id == profile.profile_id)
    result = SeatRouter(CONFIG).act(seat.seat_id, context={"agent_id": seat.seat_id}, contract={}, prompt="rules", prompt_version="v")
    assert result.action == ACTION
    assert result.meta["input_tokens"] == 31 and result.meta["output_tokens"] == 7
    assert result.meta["model_name"] == "actual-model-version"
    assert result.meta["provider"] == profile.provider
    assert len(requests) == 1


@pytest.mark.parametrize("text", ["", "```json\n{}\n```", "[]", "not-json"])
def test_parse_failure_preserves_usage_without_raw_output(text, monkeypatch):
    p = CONFIG.profiles[0]
    monkeypatch.setenv(p.api_key_env, "test-key")
    monkeypatch.setattr(ProviderClient, "_send", lambda *args: response(p.api_protocol, text))
    with pytest.raises(ModelAttemptError) as error:
        SeatRouter(CONFIG).act(1, context={"agent_id": 1}, contract={}, prompt="", prompt_version="v")
    assert error.value.meta["input_tokens"] == 31
    assert error.value.meta["success"] is False
    assert "reasoning_content" not in error.value.meta


def test_http_failure_safe_and_no_retry(monkeypatch):
    p = CONFIG.profiles[1]
    monkeypatch.setenv(p.api_key_env, "private-test-key")
    requests = []
    def fail(*args):
        requests.append(1)
        raise urllib.error.HTTPError("https://example.test", 429, "private-test-key", {}, io.BytesIO(b"private-test-key"))
    monkeypatch.setattr(ProviderClient, "_send", fail)
    with pytest.raises(ProviderError) as error:
        ProviderClient(p).complete([], agent_id="2", prompt_version="v")
    assert error.value.meta["http_status"] == 429
    assert error.value.meta["input_tokens"] is None
    assert "private-test-key" not in str(error.value) + json.dumps(error.value.meta)
    assert requests == [1]


@pytest.mark.parametrize("code,expected", [
    ("ModelNotOpen", "ModelNotOpen"),
    ("InvalidEndpointOrModel.NotFound", "InvalidEndpointOrModel.NotFound"),
    ("PathNotFound", "PathNotFound"),
    ("InvalidEndpointOrModel.ModelIDAccessDisabled", "InvalidEndpointOrModel.ModelIDAccessDisabled"),
    ("unknown-secret-code", None),
    ("1000", "1000"),
    (1003, "1003"),
])
def test_http_diagnostic_code_allowlist(code, expected, monkeypatch):
    p = next(p for p in CONFIG.profiles if p.provider == "ark")
    monkeypatch.setenv(p.api_key_env, "private-test-key")
    def fail(*args):
        body = json.dumps({"error": {"code": code, "message": "private-test-key account-info"}}).encode()
        raise urllib.error.HTTPError("https://example.test", 404, "private-test-key", {}, io.BytesIO(body))
    monkeypatch.setattr(ProviderClient, "_send", fail)
    with pytest.raises(ModelAttemptError) as caught:
        SeatRouter(CONFIG).act(5, context={"agent_id": 5}, contract={}, prompt="", prompt_version="v")
    meta = caught.value.meta
    assert meta.get("provider_error_code") == expected
    assert meta["http_status"] == 404
    assert "private-test-key" not in json.dumps(meta)
    assert "account-info" not in json.dumps(meta)
    assert "unknown-secret-code" not in json.dumps(meta)


@pytest.mark.parametrize("protocol", ["chat_completions", "responses"])
def test_truncated_answer_rejected(protocol, monkeypatch):
    p = next(p for p in CONFIG.profiles if p.api_protocol == protocol)
    monkeypatch.setenv(p.api_key_env, "test-key")
    payload = response(protocol)
    if protocol == "responses":
        payload["status"] = "incomplete"
    else:
        payload["choices"][0]["finish_reason"] = "length"
    monkeypatch.setattr(ProviderClient, "_send", lambda *args: payload)
    with pytest.raises(ProviderError):
        ProviderClient(p).complete([], agent_id="1", prompt_version="v")


def test_missing_usage_and_model_stay_unknown(monkeypatch):
    p = CONFIG.profiles[0]
    monkeypatch.setenv(p.api_key_env, "test-key")
    payload = response(p.api_protocol)
    payload.pop("usage")
    payload.pop("model")
    monkeypatch.setattr(ProviderClient, "_send", lambda *args: payload)
    _, meta = ProviderClient(p).complete([], agent_id="1", prompt_version="v")
    assert meta["model_name"] is None and meta["input_tokens"] is None


def test_missing_key_sends_no_request(monkeypatch):
    p = CONFIG.profiles[0]
    monkeypatch.delenv(p.api_key_env, raising=False)
    monkeypatch.setattr(ProviderClient, "_send", lambda *args: pytest.fail("request without key"))
    with pytest.raises(ProviderError):
        ProviderClient(p).complete([], agent_id="1", prompt_version="v")


@pytest.mark.parametrize("options", ['{"messages":[]}', '{"api_key":"fake"}', '[]'])
def test_config_rejects_request_override(options):
    with pytest.raises(ValueError):
        ModelProfile("a", "A", "mimo", "m", "KEY", request_options_json=options)


def test_transport_posts_json_and_rejects_redirects(monkeypatch):
    from app.provider_client import NoRedirect
    p = CONFIG.profiles[0]
    monkeypatch.setenv(p.api_key_env, "test-key")
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps(response(p.api_protocol)).encode()
    class Opener:
        def open(self, request, timeout):
            assert request.get_method() == "POST" and timeout == p.timeout
            return Response()
    def opener(handler):
        assert isinstance(handler, NoRedirect)
        assert handler.redirect_request(None, None, 302, "", {}, "https://other.test") is None
        return Opener()
    monkeypatch.setattr("urllib.request.build_opener", opener)
    action, _ = ProviderClient(p).complete([], agent_id="1", prompt_version="v")
    assert action == ACTION
