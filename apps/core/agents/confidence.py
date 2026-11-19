"""A model's stated confidence as the level a person reads."""

from __future__ import annotations

from openbower_schema.sheets import ConfidenceLevel

from .constants import CONFIDENCE_HIGH_AT, CONFIDENCE_LOW_AT, CONFIDENCE_MEDIUM_AT, CONFIDENCE_VERY_HIGH_AT

_LEVELS: tuple[tuple[float, ConfidenceLevel], ...] = (
    (CONFIDENCE_VERY_HIGH_AT, "very_high"),
    (CONFIDENCE_HIGH_AT, "high"),
    (CONFIDENCE_MEDIUM_AT, "medium"),
    (CONFIDENCE_LOW_AT, "low"),
)


def confidence_level(confidence: float) -> ConfidenceLevel:
    """The highest level whose bound the score clears; a NaN clears none
    and reads very low, like the floor it fails."""
    for bound, level in _LEVELS:
        if confidence >= bound:
            return level
    return "very_low"
