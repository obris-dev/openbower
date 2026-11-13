"""Signals wire shapes: what the hosted signals service returns.

The app's signals client validates every upstream body against these
before anything downstream reads it. The ones the app's /v1/logs and
/v1/signals routes answer with are in the contract (tools.schema_sync),
as roots or nested in one; the rest only the client reads.

The parts that differ by signal (an event's `data`, a setup's `filter`)
are typed per signal. Funding is the only signal today: an event is
picked by its `signal` field, and a filter is the funding filter. A
second signal joins as its own models here; the shapes that carry a
filter without a `signal` beside it (a listener's version) then need
the service to name the signal on them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# The closed value sets, as the service defines them: codegen would emit
# each as a zod enum, and an unknown value fails validation loudly rather
# than reaching a screen as a string nothing handles.
FundingStage = Literal["pre_seed", "seed", "series_a", "series_b", "series_c", "series_d_plus", "growth", "bridge"]
FundingInstrument = Literal["equity", "convertible", "debt", "grant", "token"]
ListenerStatus = Literal["active", "retrying", "held"]
# Why an event could not be judged (a match's or a preview row's error),
# or, on a listener, the error of its last failed try or of its hold.
MatchErrorKind = Literal[
    "provider_error",
    "rejected",
    "too_long",
    "invalid_output",
    "outdated_event",
    "abandoned",
    "unexpected",
    "stale_article",
    "stale_listener",
]
PreviewStatus = Literal["pending", "running", "complete", "failed", "canceled"]
PreviewFailure = Literal["slow_down", "unavailable", "deadline", "abandoned", "unexpected"]
PreviewOutcome = Literal["waiting", "passed", "matched", "unsure", "unmatched", "errored"]


class FundingCompany(BaseModel):
    """The company a raise is about, as its article states it."""

    name: str
    domain: str | None = Field(description="Its site's domain, when the article names one.")
    hq: str | None = Field(description="Where it is headquartered, as the article words it.")
    description: str | None = Field(description="What it does, in a sentence.")


class FundingEvent(BaseModel):
    """One company's raise: the facts its article states, nothing inferred."""

    company: FundingCompany
    amount: float | None = Field(description="The full amount raised; null when the article states none.")
    currency: str | None = Field(description="ISO 4217 code of the amount.")
    stage: FundingStage | None
    instruments: list[FundingInstrument]
    lead_investors: list[str]
    other_investors: list[str]


class FundingFilter(BaseModel):
    """A funding setup's fixed checks, every one optional: the stages it
    keeps, and an amount range in one currency (inclusive, never
    converted). An empty filter keeps every raise."""

    stages: list[FundingStage] | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str | None = None


# One member per signal; a union discriminated on the parent's `signal`
# once there are two.
SignalFilter = FundingFilter


class EventSource(BaseModel):
    """The article an event was read from."""

    title: str
    summary: str = Field(description="The feed's own summary of it, cut short when long.")
    url: str
    published: str | None = Field(description="ISO time the publisher dated it.")


class FundingEventWire(BaseModel):
    """A funding event: its id is the same under every listener that matched
    it and in every preview that chose it."""

    id: str
    signal: Literal["funding"]
    data: FundingEvent
    logged_at: str
    source: EventSource


# Discriminated on `signal` once a second signal joins.
SignalEventWire = FundingEventWire


class SignalKind(BaseModel):
    """One signal a listener can follow. Its filter and event are given as
    JSON Schemas, so a form can be built from the catalog alone."""

    # Any signal the service offers, known to this app's version or not: a
    # signal added later lists here without failing the catalog.
    signal: str
    filter: dict[str, Any] = Field(description="JSON Schema of the signal's filter.")
    event: dict[str, Any] = Field(description="JSON Schema of the signal's event data.")
    min_confidence_default: float = Field(description="A new setup's judge threshold when it gives none.")


class SignalLimits(BaseModel):
    max_listeners: int = Field(description="Listeners an account may have.")
    label_max_length: int
    prompt_max_length: int
    preview_max_events: int = Field(description="Events one preview may be tried on.")


class SignalsCatalog(BaseModel):
    """`GET /v1/signals`: every signal, and the limits a setup is held to."""

    signals: list[SignalKind]
    limits: SignalLimits


class SignalEventsPage(BaseModel):
    """`GET /v1/signals/{signal}/events`: newest first, to choose from."""

    items: list[SignalEventWire]
    next_cursor: str | None


class MatchErrorWire(BaseModel):
    kind: MatchErrorKind


class PreviewFailureWire(BaseModel):
    kind: PreviewFailure


class ListenerVersionWire(BaseModel):
    """One saved setup. Versions are never changed: an edit saves a new one,
    so a listener's versions are its edit log."""

    id: str
    filter: SignalFilter
    prompt: str = Field(description="What the judge reads for; empty when the filter alone decides.")
    min_confidence: float = Field(description="How sure the judge must be for a yes to match.")
    user_id: str = Field(description="Who saved it.")
    created_at: str


class ListenerWire(BaseModel):
    """A listener and its current setup."""

    id: str
    label: str
    signal: Literal["funding"]
    user_id: str = Field(description="Who created it.")
    status: ListenerStatus
    error: MatchErrorWire | None = Field(description="Why it is retrying or held; null when active.")
    version: ListenerVersionWire
    created_at: str
    updated_at: str


class ListenersPage(BaseModel):
    items: list[ListenerWire]
    next_cursor: str | None


class ListenerVersionsPage(BaseModel):
    """A listener's versions, newest first."""

    items: list[ListenerVersionWire]
    next_cursor: str | None


class MatchWire(BaseModel):
    """An event a listener matched, under the version that matched it."""

    id: str
    version_id: str
    confidence: float | None = Field(description="The judge's; null when the filter alone decided.")
    reason: str = Field(description="The judge's one sentence; empty when the filter alone decided.")
    judged_at: str
    event: SignalEventWire


class MatchesPage(BaseModel):
    """A listener's matches, oldest first. `next_cursor` is the cursor to
    poll with next: past the last match, or the one given when nothing
    new has matched (null only before the first match)."""

    items: list[MatchWire]
    next_cursor: str | None


class PreviewRowWire(BaseModel):
    """What the setup made of one chosen event."""

    outcome: PreviewOutcome = Field(
        description=(
            "passed: outside the filter. matched: the filter alone kept it, or the judge said yes with confidence "
            "at or above the run's min_confidence. unsure: the judge said yes below it. unmatched: the judge said "
            "no. errored: it could not be judged. waiting: not judged yet."
        )
    )
    confidence: float | None = Field(
        description="How sure the judge was of its own verdict, either way; null when the judge did not read it."
    )
    reason: str
    error: MatchErrorWire | None
    event: SignalEventWire


class PreviewRunWire(BaseModel):
    """A setup tried on chosen events. Complete at create when the filter
    decides every event; otherwise poll it while its rows land."""

    id: str
    signal: Literal["funding"]
    filter: SignalFilter
    prompt: str
    min_confidence: float
    user_id: str
    status: PreviewStatus
    error: PreviewFailureWire | None = Field(description="Why it failed; null unless failed.")
    retry_at: str | None = Field(description="After a slow-down: when another preview may be tried.")
    created_at: str
    finished_at: str | None
    rows: list[PreviewRowWire] = Field(description="In the order its events were chosen.")


class PreviewRunningWire(BaseModel):
    """The open preview a create was refused for: wait for it, or cancel it."""

    id: str
    status: PreviewStatus
    since: str
