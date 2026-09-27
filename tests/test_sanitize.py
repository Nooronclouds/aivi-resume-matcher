import pytest

from matcher.sanitize import JSONSanitizeError, extract_json_object

GOOD = '{"match_score": 80, "top_strengths": ["a"], "missing_skills": [], "summary": "ok."}'


@pytest.mark.parametrize("raw", [
    GOOD,
    f"```json\n{GOOD}\n```",
    f"Sure! Here is the evaluation:\n{GOOD}\nLet me know if you need more.",
    GOOD.replace('["a"]', '["a",]').replace('"ok."}', '"ok.",}'),     # trailing commas
    GOOD.replace('"summary"', '\u201csummary\u201d'),                   # smart quotes
    '{"match_score": 80, "top_strengths": ["a {b}"], "missing_skills": [], "summary": "x"}',  # braces in strings
])
def test_recovers_json(raw):
    assert extract_json_object(raw)["match_score"] == 80


@pytest.mark.parametrize("raw", [None, "", "   ", "no json here", '{"match_score": 80, "top_'])
def test_rejects_unrecoverable(raw):
    with pytest.raises(JSONSanitizeError):
        extract_json_object(raw)
