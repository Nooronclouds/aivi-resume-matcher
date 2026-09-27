"""Output contract. This model is the single source of truth: it is sent to
Gemini as the response schema AND used to validate whatever comes back."""
from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_STRENGTHS = 5
MAX_MISSING = 8
MAX_ITEM_CHARS = 100
MAX_SUMMARY_SENTENCES = 2

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


class MatchResult(BaseModel):
    # Unknown keys from the model are ignored and never reach our output.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    match_score: int = Field(
        ...,
        ge=0,
        le=100,
        description="Integer 0-100: how well the resume matches the job description.",
    )
    top_strengths: list[str] = Field(
        ...,
        max_length=MAX_STRENGTHS,
        description="Up to 5 short phrases. Each must be evidenced in the resume and relevant to the JD.",
    )
    missing_skills: list[str] = Field(
        ...,
        max_length=MAX_MISSING,
        description="Up to 8 skills/requirements from the JD with no evidence in the resume.",
    )
    summary: str = Field(
        ...,
        min_length=1,
        description="At most 2 sentences, neutral third person, in English.",
    )

    # ---- lenient-in, strict-out normalisation ---------------------------

    @field_validator("match_score", mode="before")
    @classmethod
    def _coerce_score(cls, v: Any) -> int:
        if isinstance(v, bool) or v is None:
            raise ValueError("match_score must be a number")
        if isinstance(v, str):
            v = v.strip().rstrip("%").strip()
            if "/" in v:  # "85/100"
                v = v.split("/", 1)[0].strip()
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"match_score not numeric: {v!r}") from exc
        if math.isnan(f) or math.isinf(f):
            raise ValueError("match_score must be finite")
        if 0 < f <= 1 and not float(f).is_integer():  # model returned a 0-1 ratio
            f *= 100
        return int(round(f))

    @field_validator("top_strengths", "missing_skills", mode="before")
    @classmethod
    def _clean_list(cls, v: Any, info) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):  # "a, b, c" instead of a list
            v = [p for p in re.split(r"[,;\n]", v)]
        if not isinstance(v, list):
            raise ValueError(f"{info.field_name} must be a list of strings")
        limit = MAX_STRENGTHS if info.field_name == "top_strengths" else MAX_MISSING
        seen: set[str] = set()
        out: list[str] = []
        for item in v:
            if not isinstance(item, (str, int, float)):
                continue
            s = re.sub(r"\s+", " ", str(item)).strip(" -•*\t")
            if not s:
                continue
            s = s[:MAX_ITEM_CHARS]
            key = s.casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out[:limit]

    @field_validator("summary", mode="before")
    @classmethod
    def _two_sentences(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError("summary must be a string")
        s = re.sub(r"\s+", " ", v).strip()
        sentences = _SENTENCE_SPLIT.split(s)
        return " ".join(sentences[:MAX_SUMMARY_SENTENCES])

    @classmethod
    def llm_json_schema(cls) -> dict:
        """JSON schema handed to Gemini's constrained decoding."""
        return cls.model_json_schema()
