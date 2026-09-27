"""preprocess -> prompt -> Gemini -> sanitize -> validate -> (repair once) -> grounding check"""
from __future__ import annotations

import logging
import re

from pydantic import ValidationError

from .config import Settings
from .llm_client import GeminiClient, JSONGenerator, LLMError
from .preprocess import prepare_inputs
from .prompts import SYSTEM_PROMPT, build_repair_prompt, build_user_prompt
from .sanitize import JSONSanitizeError, extract_json_object
from .schema import MatchResult

log = logging.getLogger(__name__)


class OutputValidationError(LLMError):
    code = "OUTPUT_VALIDATION_FAILED"


def parse_and_validate(raw: str | None) -> MatchResult:
    data = extract_json_object(raw)
    extras = set(data) - set(MatchResult.model_fields)
    if extras:
        log.warning("dropping unexpected keys from model output: %s", sorted(extras))
    return MatchResult.model_validate(data)


def _mentions(text: str, term: str) -> bool:
    # Boundaries that treat + # . as part of a token, so "C" != "C++", "Java" != "JavaScript".
    pattern = rf"(?<![\w+#]){re.escape(term)}(?![\w+#])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def enforce_grounding(result: MatchResult, resume: str) -> MatchResult:
    """Deterministic hallucination check: a skill can't be 'missing' if the resume names it."""
    contradicted = [s for s in result.missing_skills if _mentions(resume, s)]
    if not contradicted:
        return result
    log.warning("removed missing_skills that appear in the resume: %s", contradicted)
    kept = [s for s in result.missing_skills if s not in contradicted]
    return result.model_copy(update={"missing_skills": kept})


def evaluate(
    resume_text: str,
    jd_text: str,
    settings: Settings | None = None,
    client: JSONGenerator | None = None,
) -> MatchResult:
    settings = settings or Settings()
    prepared = prepare_inputs(resume_text, jd_text, settings)
    for w in prepared.warnings:
        log.warning(w)

    client = client or GeminiClient(settings)
    schema = MatchResult.llm_json_schema()
    user_prompt = build_user_prompt(prepared.resume, prepared.jd)

    raw, model = client.generate_json(SYSTEM_PROMPT, user_prompt, schema)
    try:
        result = parse_and_validate(raw)
    except (JSONSanitizeError, ValidationError) as first_err:
        log.warning("output from %s invalid (%s); attempting one repair call", model, first_err)
        repair = build_repair_prompt(user_prompt, raw, str(first_err))
        raw2, model = client.generate_json(SYSTEM_PROMPT, repair, schema)
        try:
            result = parse_and_validate(raw2)
        except (JSONSanitizeError, ValidationError) as second_err:
            raise OutputValidationError(f"output invalid after repair: {second_err}") from second_err

    log.info("scored with model=%s", model)
    return enforce_grounding(result, prepared.resume)
