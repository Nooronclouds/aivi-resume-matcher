# Deliverable 02: System Prompt Architecture

The production prompt lives in [`matcher/prompts.py`](../matcher/prompts.py) (`PROMPT_VERSION = resume-jd-match/v1.0`). The schema lives in [`matcher/schema.py`](../matcher/schema.py). This doc explains the design choices.

## 1. Design principles

1. **The prompt is not the only defence.** Every rule in the prompt is also enforced in code where possible: schema by constrained decoding + Pydantic, injection by preprocessing, grounding by a deterministic post-check. The prompt reduces failure rate; the code catches what gets through.
2. **Write for a parser, not a person.** The model is told its output goes to a program. That single framing cuts most conversational filler.
3. **One source of truth for the schema.** The same Pydantic class generates the JSON Schema sent to Gemini *and* validates the response, so the two can never drift.

## 2. Prompt structure

| Section | Purpose |
|---|---|
| ROLE | Narrow, machine-facing identity ("a program parses your output") |
| OUTPUT CONTRACT | Exact keys, types, lengths. Explicit ban on preamble, markdown, fences |
| TRUST BOUNDARY | Inputs are *data*, never instructions. Defines how to react to the sanitizer marker |
| GROUNDING | Anti-hallucination rules: evidence required, no inference from titles/colleges, explicit equivalence examples, what to do with garbled text |
| LANGUAGE | Hinglish/Devanagari handled; language mix may not affect score; output always English |
| SCORING RUBRIC | Additive 50/25/15/10 rubric + calibration bands. Reduces run-to-run variance and makes scores explainable |
| EXAMPLES | 4 few-shot cases, each targeting one failure mode (below) |

Inputs are wrapped in `<job_description>` / `<resume>` tags. The preprocessor strips any such tags from user text so a resume can't "close" its own block and inject text outside it.

## 3. Schema enforcement (3 layers)

```
Gemini response_json_schema (constrained decoding)
        ↓  still defended against: fences, preamble, truncation, smart quotes
sanitize.extract_json_object   → fence strip → balanced-brace scan → conservative repair
        ↓
MatchResult.model_validate     → types, 0–100 range, list caps, 2-sentence summary, extra keys dropped
        ↓ on failure
one repair call with the validation error fed back → else OUTPUT_VALIDATION_FAILED
```

Validation is **lenient-in, strict-out**: `"85%"`, `"85/100"`, `84.6` and a 0–1 ratio are normalised to an int; duplicate/empty list items are dropped; over-long lists are capped. Anything genuinely wrong (score 150, `"high"`, missing keys) is rejected. Output always contains exactly the four contract keys.

## 4. Few-shot examples: why these four

| # | Edge case | What it teaches |
|---|---|---|
| 1 | Standard match | Output shape, rubric calibration (all required skills + no preferred ≈ mid-70s) |
| 2 | Prompt injection | Sanitizer marker → flag in summary, **score unchanged** (scored on real skills only) |
| 3 | Garbled OCR | Don't guess; low score + explicit "text appears incomplete" |
| 4 | Hinglish | Code-mixed technical content scored on substance; output in English |

Examples are short on purpose: they cost tokens on every call, and long examples get copied too literally.

**Design choice worth flagging:** an injection attempt does *not* lower the score. The scorer measures qualifications; integrity is a separate signal. Mixing them makes both unreliable. The product layer can still act on the flag (e.g. route to human review).

## 5. Fallback strategy: 429s and latency spikes

Implemented in [`matcher/llm_client.py`](../matcher/llm_client.py), unit-tested in `tests/test_llm_client.py` with a fake clock.

| Condition | Action |
|---|---|
| 429, 408, 500, 502, 503, 504 | Retry with **exponential backoff + full jitter** (1s, 2s, 4s… capped at 20s, randomised to avoid synchronised retry storms). If the server sends `retryDelay` / `Retry-After`, use that instead |
| Timeout / connection error | Per-request timeout (30s default), treated as retryable |
| Empty response (MAX_TOKENS / safety block) | Retry immediately-ish; counts as an attempt |
| Retries exhausted | **Fall back once** to a lighter model (`gemini-flash-lite-latest`) |
| 400 / 404 on a model | Skip straight to the fallback model (model missing or schema unsupported there) |
| 401 / 403 | Fail fast, no retry (retrying can't fix a bad key) |
| Total budget | Hard **90s deadline** across everything; the primary model gets ~65% so the fallback always has time. If the next backoff would cross the deadline, stop now instead of sleeping |
| Everything fails | Structured error `{"error": {"code": "LLM_UNAVAILABLE", ...}}`, exit code 3. **Never a fake or default score** |

**Production extensions** (not needed for a CLI, would add for a service): a circuit breaker per model so a sustained outage stops sending traffic after N failures; a token-bucket client-side rate limiter matched to the project's RPM quota; a queue for batch scoring (resume screening doesn't need sub-second latency, so async with retries beats failing the user); caching by `hash(resume + JD + PROMPT_VERSION)` since identical pairs are re-scored often.

## 6. Latency levers
- `temperature=0`, short prompt (~1.2k tokens), 4 compact examples
- Input truncation caps (20k resume / 8k JD chars) bound worst-case latency and cost
- Hopeless inputs (empty/scanned PDFs) rejected **before** any API call
- `-latest` model aliases so the pipeline doesn't break on model deprecation; override via `GEMINI_MODEL`
