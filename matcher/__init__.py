"""AIVI resume <-> JD matcher (Gemini)."""
from .pipeline import evaluate
from .schema import MatchResult

__all__ = ["evaluate", "MatchResult"]
