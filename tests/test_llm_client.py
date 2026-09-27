from types import SimpleNamespace

import pytest
from google.genai import errors

from matcher.config import Settings
from matcher.llm_client import GeminiClient, LLMAuthError, LLMUnavailableError


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def make(outcomes, **settings_kw):
    """outcomes: per call, either a str (response text) or an exception."""
    clock = Clock()
    s = Settings(api_key="x", primary_model="primary", fallback_model="fallback", **settings_kw)
    c = GeminiClient(s, sleep=clock.sleep, clock=clock)
    calls = []

    def generate_content(model, contents, config):
        calls.append(model)
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return SimpleNamespace(text=o, candidates=[SimpleNamespace(finish_reason="STOP")])

    c._client = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    return c, calls, clock


def api_err(code, details=None):
    return errors.APIError(code, {"error": {"code": code, "message": "x", "details": details or []}})


def test_retries_429_then_succeeds():
    c, calls, _ = make([api_err(429), api_err(503), "{}"])
    assert c.generate_json("s", "u", {}) == ("{}", "primary")
    assert calls == ["primary"] * 3


def test_honours_server_retry_delay():
    c, _, clock = make([api_err(429, [{"retryDelay": "7s"}]), "{}"])
    c.generate_json("s", "u", {})
    assert clock.t == pytest.approx(7.0)


def test_falls_back_after_exhausting_retries():
    c, calls, _ = make([api_err(429)] * 4 + ["{}"])
    assert c.generate_json("s", "u", {})[1] == "fallback"
    assert calls == ["primary"] * 4 + ["fallback"]


def test_model_not_found_goes_straight_to_fallback():
    c, calls, _ = make([api_err(404), "{}"])
    assert c.generate_json("s", "u", {})[1] == "fallback"


def test_auth_error_fails_fast():
    c, calls, _ = make([api_err(401)])
    with pytest.raises(LLMAuthError):
        c.generate_json("s", "u", {})
    assert len(calls) == 1


def test_deadline_stops_retry_storm():
    c, calls, _ = make([api_err(429, [{"retryDelay": "15s"}])] * 10, total_deadline_s=20)
    with pytest.raises(LLMUnavailableError):
        c.generate_json("s", "u", {})
    assert len(calls) <= 3


def test_empty_response_is_retried():
    c, calls, _ = make(["", "{}"])
    assert c.generate_json("s", "u", {})[0] == "{}"
