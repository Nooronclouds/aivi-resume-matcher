"""Runtime settings, all overridable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    )
    # "-latest" aliases avoid hard-coding a model version that gets deprecated.
    primary_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))
    fallback_model: str = field(
        default_factory=lambda: _env("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest")
    )

    # Latency / resilience
    request_timeout_s: float = field(default_factory=lambda: _env_float("GEMINI_TIMEOUT_S", 30.0))
    max_retries: int = field(default_factory=lambda: _env_int("GEMINI_MAX_RETRIES", 4))
    base_backoff_s: float = 1.0
    max_backoff_s: float = 20.0
    total_deadline_s: float = field(default_factory=lambda: _env_float("PIPELINE_DEADLINE_S", 90.0))

    # Generation
    temperature: float = 0.0  # deterministic scoring
    max_output_tokens: int = 4096  # generous: thinking models spend tokens before the JSON

    # Input guards
    max_resume_chars: int = 20_000
    max_jd_chars: int = 8_000
    min_resume_chars: int = 150
    min_jd_chars: int = 40
