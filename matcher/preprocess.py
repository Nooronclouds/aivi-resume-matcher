"""Input hardening that runs BEFORE any tokens are spent.

Layer 1 of prompt-injection defence (layer 2 is the system prompt's trust
boundary, layer 3 is output validation). Also rejects inputs that are too thin
to score honestly, e.g. text extracted from a scanned, image-only PDF.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .config import Settings

INJECTION_MARKER = "[instruction-like text removed by sanitizer]"

# English + Hinglish patterns. Line-level: a matching line is replaced whole.
_INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instructions?|rules?|prompts?|guidelines?|criteria)\b",
    r"\b(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?)\b",
    r"\byou\s+are\s+now\b",
    r"\b(system|developer)\s*(prompt|message|mode)\b",
    r"^\s*(system|assistant)\s*:",
    r"\bnew\s+instructions?\b",
    r"\b(give|assign|set|output|return|rate)\b.{0,30}\b(score|rating|marks|match)\b.{0,20}\b(100|hundred|max(imum)?)\b",
    r"\b(give|assign)\b.{0,30}\b(100|full marks|perfect score)\b",
    r"\b(match_score|top_strengths|missing_skills)\b",  # our output keys never belong in a resume
    # Hinglish
    r"\b(rules?|instructions?)\b.{0,20}\b(ignore|bhool)\s*(karo|kar do|kardo|jao)\b",
    r"\b100\b\s*(marks?|score|number|points)?\s*(de\s*do|dedo|dena)\b",
    r"\b(pura|poora|full)\s+(score|marks?)\b",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.IGNORECASE)

# Zero-width / bidi characters are a classic way to hide instructions.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_DELIMITER_TAGS = re.compile(r"</?\s*(resume|job_description)\b[^>]*>", re.IGNORECASE)


class InputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class PreparedInputs:
    resume: str
    jd: str
    warnings: list[str] = field(default_factory=list)
    injection_lines: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    text = _DELIMITER_TAGS.sub("", text)  # can't close our <resume> tag early
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def neutralize_injections(text: str) -> tuple[str, list[str]]:
    hits: list[str] = []
    out: list[str] = []
    for line in text.splitlines():
        if _INJECTION_RE.search(line):
            hits.append(line.strip())
            out.append(INJECTION_MARKER)
        else:
            out.append(line)
    return "\n".join(out), hits


def _legibility_ratio(text: str) -> float:
    """Share of non-space chars that are letters/digits. Garbled OCR scores low."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(c.isalnum() for c in chars) / len(chars)


def prepare_inputs(resume: str, jd: str, settings: Settings) -> PreparedInputs:
    if not isinstance(resume, str) or not isinstance(jd, str):
        raise InputError("INVALID_INPUT", "resume and job description must be text")

    warnings: list[str] = []
    resume_n, jd_n = normalize(resume), normalize(jd)

    if len(resume_n) < settings.min_resume_chars:
        raise InputError(
            "INSUFFICIENT_RESUME_TEXT",
            f"resume has {len(resume_n)} usable characters (min {settings.min_resume_chars}); "
            "if this came from a PDF it is probably scanned/image-only and needs OCR",
        )
    if len(jd_n) < settings.min_jd_chars:
        raise InputError("INSUFFICIENT_JD_TEXT", "job description is too short to score against")

    if (ratio := _legibility_ratio(resume_n)) < 0.6:
        warnings.append(f"resume text looks garbled (legibility {ratio:.2f}); possible bad OCR")

    resume_n, hits = neutralize_injections(resume_n)
    if hits:
        warnings.append(f"neutralized {len(hits)} instruction-like line(s) in resume")
    jd_n, jd_hits = neutralize_injections(jd_n)
    if jd_hits:
        warnings.append(f"neutralized {len(jd_hits)} instruction-like line(s) in JD")

    if len(resume_n) > settings.max_resume_chars:
        resume_n = resume_n[: settings.max_resume_chars]
        warnings.append(f"resume truncated to {settings.max_resume_chars} chars")
    if len(jd_n) > settings.max_jd_chars:
        jd_n = jd_n[: settings.max_jd_chars]
        warnings.append(f"JD truncated to {settings.max_jd_chars} chars")

    return PreparedInputs(resume_n, jd_n, warnings, hits + jd_hits)
