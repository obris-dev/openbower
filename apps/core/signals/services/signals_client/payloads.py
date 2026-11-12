"""The request bodies the signals service takes, built one way for every
caller."""

from __future__ import annotations

from typing import Any


def setup_payload(*, filter: dict[str, Any], prompt: str | None, min_confidence: float | None) -> dict[str, Any]:
    """A setup as the service takes it: given whole, the filter with its
    prompt and threshold when there are any (an absent one takes the
    service's default)."""
    setup: dict[str, Any] = {"filter": filter}
    if prompt is not None:
        setup["prompt"] = prompt
    if min_confidence is not None:
        setup["min_confidence"] = min_confidence
    return setup
