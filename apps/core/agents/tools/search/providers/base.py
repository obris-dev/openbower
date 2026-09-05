"""The provider CONTRACT: what any search provider declares to
register, and the shapes every provider speaks. Nothing tool-shaped
lives here: a provider knows how to run ONE query and declares the
facts the seam judges it by (its credential keys, its cost, its
timeout), and the harness owns the retry schedule and the family's
typed failures.

A provider is a module declaring a SPEC (`ProviderSpec`) and
registering it in `providers.registry`, the same shape a tool takes:
adding a provider is adding a module and one register() call. Its
`run` speaks in ATTEMPT signals, one try's story only: whether a
refusal becomes a retry or a decided failure is the harness's call,
never the provider's."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import NamedTuple

from ....constants import SEARCH_TIMEOUT_SECONDS

# A registered provider name is a code token (the tools config's
# section and wiring value, the wire's provider slot): bounded like
# every authored value (binary, far above any real name).
PROVIDER_NAME_MAX_LENGTH = 64


class AttemptThrottled(Exception):
    """ONE try was told to slow down. `retry_after` is the provider's
    own ask in seconds when it made one (DataForSEO's header); the
    harness honors it clamped to the schedule's longest step, and
    takes the schedule's step otherwise."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AttemptUnreachable(Exception):
    """ONE try could not reach the provider or heard nothing in time:
    a connect or read timeout, a dropped connection. For providers
    whose transport does not speak httpx (the harness reads httpx's
    own transport errors directly)."""


def parse_retry_after(value: str) -> float | None:
    """A Retry-After header's delay-seconds form only; the HTTP-date
    form is rare enough on these vendors that it takes the schedule's
    step. A negative, NaN, or infinite delay is a hostile or broken
    header, not a schedule: NaN would poison min() and a negative
    wait raises out of sleep as a settled model error."""
    try:
        parsed = float(value) if value else None
    except ValueError:
        return None
    if parsed is None or not 0 <= parsed < float("inf"):
        return None
    return parsed


class SearchHit(NamedTuple):
    title: str
    url: str
    snippet: str


class ProviderAnswer(NamedTuple):
    """What one SERVED query came back with: the hits (empty being an
    honest zero-hit answer), which provider, and how many tries this
    call made (1 when clean; more means the retry schedule bought it,
    which the runtime reads as a rate signal). FAILURES never take
    this shape: a failure raises the family's typed error the moment
    the schedule decides it, carrying the same provider and attempts
    for the audit."""

    hits: list[SearchHit]
    provider: str
    attempts: int


@dataclass(frozen=True, kw_only=True)
class ProviderSpec:
    """One search provider: its live call plus the DECLARED facts the
    seam needs before any call. The NAME is the run function's own
    name (a property, never declared): the config file's section and
    wiring value, the name every stored call records. Usability is
    DERIVED, never declared: the registry reads the provider as usable
    when its vendor table carries every key in config_keys (an empty
    tuple declares keyless), so a new provider can never fall through
    to keyless-by-default."""

    # ONE query against the live provider: (query, count) -> hits.
    # Raises AttemptThrottled for a refusal the harness should retry
    # and AttemptUnreachable (or httpx's transport errors) for a
    # provider it could not reach; anything else it raises is a
    # per-query error.
    run: Callable[[str, int], list[SearchHit]]
    # The SCHEMA of the vendor's config table: a frozen dataclass
    # of str fields declared in the vendor's own module beside run
    # (None declares keyless). The operator-facing key names derive
    # from its fields (config_keys below), and run reads a
    # constructed instance, so a key exists once as a typed attribute
    # and a typo'd read is a loud AttributeError, never a silent "".
    config_schema: type | None = None
    # Whether a query costs money: the free-search fill budget caps
    # fills only while web search is served unmetered, and failure
    # copy offers a metered vendor as the throughput remedy.
    metered: bool
    # The run call's own transport timeout: the general quick-or-dead
    # search budget by default, overridden ON THE SPEC by a vendor
    # that computes per request. The family's worst-case bound derives
    # from the registry's maximum, so a bigger declaration here raises
    # it by itself.
    timeout_seconds: int = SEARCH_TIMEOUT_SECONDS
    # The vendor's name as failure copy and operator surfaces print
    # it.
    display: str

    @property
    def name(self) -> str:
        return self.run.__name__

    @property
    def config_keys(self) -> tuple[str, ...]:
        """The vendor table's key names, DERIVED from the config
        schema's fields (declaration order), never restated."""
        if self.config_schema is None:
            return ()
        return tuple(field.name for field in fields(self.config_schema))
