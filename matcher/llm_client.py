"""Gemini wrapper with the resilience policy from docs/02 section 5:

- 429 / 5xx / timeouts -> exponential backoff with full jitter, honouring the
  server's retryDelay / Retry-After when present
- a total deadline so latency spikes can't stall the caller indefinitely
- after retries are exhausted (or the model is unavailable/unsupported),
  fall back once to a lighter model
- auth errors fail fast (retrying won't help)
"""
from __future__ import annotations

import logging
import random
import re
import time
from typing import Callable, Protocol

import httpx
from google import genai
from google.genai import errors, types

from .config import Settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
FALLBACK_STATUS = {400, 404}  # model missing / schema unsupported on this model
FATAL_STATUS = {401, 403}
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s")


class LLMError(Exception):
    code = "LLM_ERROR"


class LLMAuthError(LLMError):
    code = "LLM_AUTH_ERROR"


class LLMUnavailableError(LLMError):
    code = "LLM_UNAVAILABLE"


class JSONGenerator(Protocol):
    """Anything that can do this can drive the pipeline (lets tests use a fake)."""

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict) -> tuple[str, str]:
        ...


def _server_retry_delay(exc: errors.APIError) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if headers:
        ra = headers.get("retry-after")
        if ra:
            try:
                return float(ra)
            except ValueError:
                pass
    m = _RETRY_DELAY_RE.search(str(getattr(exc, "details", "")) + str(exc))
    return float(m.group(1)) if m else None


class GeminiClient:
    def __init__(
        self,
        settings: Settings,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not settings.api_key:
            raise LLMAuthError("GEMINI_API_KEY is not set")
        self.s = settings
        self._sleep, self._clock = sleep, clock
        self._client = genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(timeout=int(settings.request_timeout_s * 1000)),
        )

    # -- public -----------------------------------------------------------

    def generate_json(self, system_prompt: str, user_prompt: str, schema: dict) -> tuple[str, str]:
        start = self._clock()
        overall = start + self.s.total_deadline_s
        models = list(dict.fromkeys([self.s.primary_model, self.s.fallback_model]))
        # Primary gets ~65% of the budget so the fallback always has time left.
        budgets = [start + 0.65 * self.s.total_deadline_s, overall] if len(models) > 1 else [overall]
        last: LLMError | None = None
        for model, deadline in zip(models, budgets):
            try:
                return self._call_with_retries(model, system_prompt, user_prompt, schema, deadline), model
            except LLMUnavailableError as exc:
                log.warning("model %s unavailable: %s", model, exc)
                last = exc
        raise last or LLMUnavailableError("no model configured")

    # -- internals --------------------------------------------------------

    def _config(self, system_prompt: str, schema: dict) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=self.s.temperature,
            max_output_tokens=self.s.max_output_tokens,
            response_mime_type="application/json",
            response_json_schema=schema,
        )

    def _backoff(self, attempt: int, server_hint: float | None) -> float:
        if server_hint is not None:
            return min(server_hint, self.s.max_backoff_s)
        cap = min(self.s.max_backoff_s, self.s.base_backoff_s * 2 ** (attempt - 1))
        return random.uniform(0, cap)  # full jitter: avoids synchronized retry storms

    def _call_with_retries(self, model, system_prompt, user_prompt, schema, deadline) -> str:
        config = self._config(system_prompt, schema)
        reason = "unknown"
        for attempt in range(1, self.s.max_retries + 1):
            if self._clock() >= deadline:
                raise LLMUnavailableError(f"deadline exceeded before attempt {attempt}")
            hint: float | None = None
            t0 = self._clock()
            try:
                resp = self._client.models.generate_content(
                    model=model, contents=user_prompt, config=config
                )
                text = resp.text
                log.info("model=%s attempt=%d latency=%.2fs", model, attempt, self._clock() - t0)
                if text and text.strip():
                    return text
                # Empty text: usually MAX_TOKENS or a safety block. Retry quickly.
                finish = getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None
                reason = f"empty response (finish_reason={finish})"
            except errors.APIError as exc:
                status = getattr(exc, "code", None)
                if status in FATAL_STATUS:
                    raise LLMAuthError(f"{status}: {exc}") from exc
                if status in FALLBACK_STATUS:
                    raise LLMUnavailableError(f"{status}: {exc}") from exc
                if status not in RETRYABLE_STATUS:
                    raise LLMError(f"non-retryable {status}: {exc}") from exc
                hint = _server_retry_delay(exc)
                reason = f"HTTP {status}"
            except (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError) as exc:
                reason = f"transport: {type(exc).__name__}"

            if attempt == self.s.max_retries:
                break
            wait = self._backoff(attempt, hint)
            if self._clock() + wait >= deadline:
                raise LLMUnavailableError(f"{reason}; next retry would exceed deadline")
            log.warning("model=%s attempt=%d failed (%s); retrying in %.1fs", model, attempt, reason, wait)
            self._sleep(wait)

        raise LLMUnavailableError(f"{reason} after {self.s.max_retries} attempts")
