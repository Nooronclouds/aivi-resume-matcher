import pytest
from pydantic import ValidationError

from matcher.schema import MatchResult

BASE = {"match_score": 70, "top_strengths": ["x"], "missing_skills": ["y"], "summary": "Fine."}


@pytest.mark.parametrize("score,expected", [("85", 85), ("85%", 85), ("85/100", 85), (84.6, 85), (0.72, 72)])
def test_score_coercion(score, expected):
    assert MatchResult.model_validate({**BASE, "match_score": score}).match_score == expected


@pytest.mark.parametrize("score", [150, -5, "high", None, True])
def test_score_rejected(score):
    with pytest.raises(ValidationError):
        MatchResult.model_validate({**BASE, "match_score": score})


def test_lists_cleaned_deduped_capped():
    r = MatchResult.model_validate({**BASE, "top_strengths": ["Python", "python ", "", "- SQL", "a", "b", "c", "d"]})
    assert r.top_strengths == ["Python", "SQL", "a", "b", "c"]


def test_comma_string_becomes_list():
    assert MatchResult.model_validate({**BASE, "missing_skills": "Docker, AWS"}).missing_skills == ["Docker", "AWS"]


def test_summary_capped_to_two_sentences():
    r = MatchResult.model_validate({**BASE, "summary": "One. Two. Three."})
    assert r.summary == "One. Two."


def test_extra_keys_dropped_and_missing_keys_rejected():
    assert "reasoning" not in MatchResult.model_validate({**BASE, "reasoning": "x"}).model_dump()
    with pytest.raises(ValidationError):
        MatchResult.model_validate({"match_score": 50})
