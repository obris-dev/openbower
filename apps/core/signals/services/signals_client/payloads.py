"""The request bodies the signals service takes, built one way for every
caller."""

from __future__ import annotations

from typing import Any

from openbower_schema.signals import SERVICE_TYPE_KEY


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


def listener_payload(*, label: str, signal_type: str, setup: dict[str, Any]) -> dict[str, Any]:
    """A listener as the service takes it: its label, its type, and a setup
    from setup_payload."""
    return {"label": label, SERVICE_TYPE_KEY: signal_type, **setup}


def preview_payload(*, signal_type: str, event_ids: list[str], setup: dict[str, Any]) -> dict[str, Any]:
    """A preview as the service takes it: its type, a setup from
    setup_payload, and the events to try it on, in the order to show them."""
    return {SERVICE_TYPE_KEY: signal_type, "event_ids": event_ids, **setup}
