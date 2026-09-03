"""The provider CONTRACT: what any search provider declares to
register, and the shapes every provider speaks. Nothing tool-shaped
lives here: a provider knows exactly two things, how to run ONE query
and whether THIS deploy can use it, and the harness owns the retry
schedule and the family's typed failures.

A provider is a module declaring a SPEC (`ProviderSpec`) and
registering it in `providers.registry`, the same shape a tool takes:
adding a provider is adding a module and one register() call. Its
`run` speaks in ATTEMPT signals, one try's story only: whether a
refusal becomes a retry or a decided failure is the harness's call,
never the provider's."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

# A registered provider name is a code token (the settings switch's
# value, the wire's provider slot): bounded like every authored value
# (binary, far above any real name).
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


@dataclass(frozen=True)
class ProviderSpec:
    """One search provider: its live call AND its usability, declared
    together so registering a provider forces both questions (a new
    provider can never fall through to keyless-by-default). The NAME
    is the run function's own name (a property, never declared): the
    settings switch's value, the name every stored call records."""

    # ONE query against the live provider: (query, count) -> hits.
    # Raises AttemptThrottled for a refusal the harness should retry
    # and AttemptUnreachable (or httpx's transport errors) for a
    # provider it could not reach; anything else it raises is a
    # per-query error.
    run: Callable[[str, int], list[SearchHit]]
    # Whether THIS deploy can use the provider, asked before any call:
    # a callable because credentials are settings read at ask time.
    usable: Callable[[], bool]

    @property
    def name(self) -> str:
        return self.run.__name__
