"""Signals wire shapes: what the hosted signals service returns.

The app's signals client validates every upstream body against these
before anything downstream reads it. The ones the app's /v1/signals,
/v1/signal-types and /v1/signal-previews routes answer with are in the
contract (tools.schema_sync), as roots or nested in one; the rest only
the client reads. The app's /v1/signal-types is the service's
/v1/signals, passed through.

The service names a signal's type `signal` (`SERVICE_TYPE_KEY`); the
app's wire names it `type`. Each model that carries it reads the
service's key and writes the app's (`_SERVICE_TYPE`, under
`_READS_SERVICE_KEYS`), so the client validates an upstream body
unchanged and a view dumps it as the app's shape: there is one model per
shape, not an upstream and an app copy. The catalog's list reads
`signals` and writes `types` the same way.

The parts that differ by type (an event's `data`, a setup's `filter`)
are typed per type. Funding is the only type today: an event is picked by
its `type`, and a filter is the funding filter. A second type joins as
its own models here; the shapes that carry a filter without a type beside
it (a listener's version) then need the service to name the type on them.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Keys the service names differently from the app's wire (a type is its
# `signal`, the catalog's list its `signals`): read from either key (the
# service's body, or the app's own when a test builds one), written as the
# app's.
_READS_SERVICE_KEYS = ConfigDict(validate_by_name=True, validate_by_alias=True)
# The key the service names a signal's type under, read here and written by
# the app's payloads to the service.
SERVICE_TYPE_KEY = "signal"
_SERVICE_TYPE = Field(
    validation_alias=SERVICE_TYPE_KEY,
    description="The signal's type, as the catalog names it (`SignalsCatalog.types[].type`).",
)


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


# One member per type; a union discriminated on the parent's `type` once
# there are two.
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

    model_config = _READS_SERVICE_KEYS

    id: str
    type: Annotated[Literal["funding"], _SERVICE_TYPE]
    data: FundingEvent
    logged_at: str
    source: EventSource


# Discriminated on `type` once a second type joins.
SignalEventWire = FundingEventWire


class SignalKind(BaseModel):
    """One signal type a listener can follow. Its filter and event are given as
    JSON Schemas, so a form can be built from the catalog alone."""

    model_config = _READS_SERVICE_KEYS

    # Any type the service offers, known to this app's version or not: a
    # type added later lists here without failing the catalog.
    type: Annotated[str, _SERVICE_TYPE, Field(description="The type's key, as listeners, events and previews name it.")]
    filter: dict[str, Any] = Field(description="JSON Schema of the signal's filter.")
    event: dict[str, Any] = Field(description="JSON Schema of the signal's event data.")
    min_confidence_default: float = Field(description="A new setup's judge threshold when it gives none.")


class SignalLimits(BaseModel):
    max_listeners: int = Field(description="Listeners an account may have.")
    label_max_length: int
    prompt_max_length: int
    preview_max_events: int = Field(description="Events one preview may be tried on.")


class SignalsCatalog(BaseModel):
    """The app's `GET /v1/signal-types` (the service's `GET /v1/signals`):
    every signal type, and the limits a setup is held to."""

    model_config = _READS_SERVICE_KEYS

    types: list[SignalKind] = Field(validation_alias="signals")
    limits: SignalLimits


class SignalEventsPage(BaseModel):
    """The app's `GET /v1/signal-types/{type}/events` (the service's
    `GET /v1/signals/{signal}/events`): newest first, to choose from."""

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

    model_config = _READS_SERVICE_KEYS

    id: str
    label: str
    type: Annotated[Literal["funding"], _SERVICE_TYPE]
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

    model_config = _READS_SERVICE_KEYS

    id: str
    type: Annotated[Literal["funding"], _SERVICE_TYPE]
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
