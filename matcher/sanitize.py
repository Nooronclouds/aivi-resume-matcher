"""Turn whatever text the model returned into a Python dict, or fail loudly.

Constrained decoding makes clean JSON the common case, but production traffic
still sees: markdown fences, preambles ("Sure! Here's the JSON:"), trailing
commas, smart quotes, truncated output. We handle each explicitly.
"""
from __future__ import annotations

import json
import re

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_SMART_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})
_PY_LITERALS = [(re.compile(r"\bTrue\b"), "true"), (re.compile(r"\bFalse\b"), "false"),
                (re.compile(r"\bNone\b"), "null")]


class JSONSanitizeError(ValueError):
    """Raised when no JSON object can be recovered from the model output."""


def _first_balanced_object(text: str) -> str | None:
    """Return the first top-level {...} span, respecting strings and escapes."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        start = text.find("{", start + 1)  # unbalanced; try the next brace
    return None


def _repair(candidate: str) -> str:
    fixed = candidate.translate(_SMART_QUOTES)
    fixed = _TRAILING_COMMA.sub(r"\1", fixed)
    for pattern, repl in _PY_LITERALS:
        fixed = pattern.sub(repl, fixed)
    return fixed


def _try_load(s: str) -> dict | None:
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def extract_json_object(raw: str | None) -> dict:
    if raw is None or not raw.strip():
        raise JSONSanitizeError("model returned empty output")

    text = raw.strip().lstrip("\ufeff")

    # 1. Happy path
    if (obj := _try_load(text)) is not None:
        return obj

    # 2. Inside a markdown fence
    fence = _FENCE.search(text)
    if fence and (obj := _try_load(fence.group(1).strip())) is not None:
        return obj

    # 3. First balanced object anywhere (handles preamble / postamble)
    candidate = _first_balanced_object(text)
    if candidate is None:
        raise JSONSanitizeError("no complete JSON object found (output may be truncated)")
    if (obj := _try_load(candidate)) is not None:
        return obj

    # 4. Conservative syntactic repair
    if (obj := _try_load(_repair(candidate))) is not None:
        return obj

    raise JSONSanitizeError("found a JSON-like object but could not parse it")
